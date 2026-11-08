"""Workbook ingestion, safety validation, profiling and the upload wizard's inspection (Excel, CSV, Parquet).

An upload is read into a raw table, mapped onto the app's canonical long schema (`core/mapping.py`, long or wide
layout), checked, and profiled. The returned DataFrame is always canonical: `date`, `channel`, `sales_by_channel`,
`costs_by_channel`, `impressions_by_channel`, `clicks_by_channel`, plus `kpi_total` (wide tables) and `ctrl_*` (the
user's controls) per date.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.core.features import default_holdout_days
from mmm_studio.core.mapping import (
    TARGET_COLUMN,
    WorkbookValidationError,
    canonicalise_report,
    column_kind,
    json_records,
    mapping_errors,
    profile_columns,
    suggest_mapping,
)
from mmm_studio.core.model_settings import MERIDIAN_ROI_PRIOR, MIN_HOLDOUT_DAYS, MIN_TRAINING_DAYS
from mmm_studio.core.resample import (
    PreparationError,
    column_choices,
    describe_dates,
    method_catalog,
    resample_errors,
)
from mmm_studio.core.schema import (
    ChannelConfig,
    ColumnMapping,
    DataProfile,
    DataRoleConfig,
    infer_channel_configs,
)

__all__ = [
    "INPUT_KEYS",
    "WorkbookValidationError",
    "apply_channel_choices",
    "default_holdout_days",
    "default_prior_over_100",
    "input_key",
    "inspect_workbook",
    "profile_workbook",
    "read_raw_dataframe",
    "validate_upload_bytes",
]

MAX_DECOMPRESSED_BYTES = 150 * 1024 * 1024  # 150 MB zip-bomb ceiling
MAX_ROWS = 500_000
MAX_COLS = 100
MAX_SHEETS = 30
# Other sheets this small are shown to the user and Gemini as side tables (e.g. channel notes, attributions)
SIDE_TABLE_MAX_ROWS = 200
SIDE_TABLE_PREVIEW_ROWS = 20
SAMPLE_ROWS = 8
# Preferred default data sheet name when present in an uploaded workbook
DEFAULT_DATA_SHEET = "data"
STANDARD_COLUMNS = ColumnMapping()
ALLOWED_EXTENSIONS = {".xlsx", ".csv", ".parquet"}


def input_key(filename: str) -> str:
    """Where a run keeps its upload: `input/workbook.<ext>` (`.xlsx`, `.csv` or `.parquet`, from the upload's name)."""
    ext = Path(filename).suffix.lower()
    return f"input/workbook{ext if ext in ALLOWED_EXTENSIONS else '.xlsx'}"


INPUT_KEYS: tuple[str, ...] = tuple(f"input/workbook{ext}" for ext in (".xlsx", ".csv", ".parquet"))


def validate_upload_bytes(filename: str, data: bytes, max_upload_mb: int = 25) -> str:
    """Validate extension, byte size and zip-bomb bounds; return the extension (the format to read)."""
    if not data:
        raise WorkbookValidationError("Uploaded file is empty.")
    max_bytes = max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise WorkbookValidationError(
            f"Uploaded file exceeds maximum allowed size of {max_upload_mb} MB."
        )

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise WorkbookValidationError(
            f"Unsupported file format '{ext}'. Upload .xlsx, .csv, or .parquet."
        )
    fmt = ext

    if fmt == ".xlsx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                infos = zf.infolist()
                if not infos:
                    raise WorkbookValidationError("Empty .xlsx archive.")
                if len(infos) > 256:
                    raise WorkbookValidationError("XLSX archive contains too many entries.")
                total_uncompressed = sum(info.file_size for info in infos)
                if total_uncompressed > MAX_DECOMPRESSED_BYTES:
                    raise WorkbookValidationError(
                        "XLSX archive uncompressed size exceeds safety limit (150 MB)."
                    )
                actual_bytes = 0
                for info in infos:
                    with zf.open(info) as member:
                        while True:
                            chunk = member.read(65536)
                            if not chunk:
                                break
                            actual_bytes += len(chunk)
                            if actual_bytes > MAX_DECOMPRESSED_BYTES:
                                raise WorkbookValidationError(
                                    "Decompressed .xlsx stream exceeds safety limit (150 MB)."
                                )
        except zipfile.BadZipFile as exc:
            raise WorkbookValidationError("Corrupted or invalid .xlsx workbook.") from exc

    return fmt


def _check_size(df: pd.DataFrame, what: str = "Dataset") -> None:
    if len(df) > MAX_ROWS:
        raise WorkbookValidationError(f"{what} row count ({len(df)}) exceeds limit ({MAX_ROWS}).")
    if df.shape[1] > MAX_COLS:
        raise WorkbookValidationError(f"{what} column count ({df.shape[1]}) exceeds limit ({MAX_COLS}).")


def _read_csv(buf: io.BytesIO) -> pd.DataFrame:
    """CSV with the separator sniffed (`,` `;` tab `|`) and any UTF-8 BOM stripped."""
    try:
        return pd.read_csv(buf, sep=None, engine="python", encoding="utf-8-sig")
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
        raise WorkbookValidationError("The CSV file could not be read as a table with a header row.") from exc


def read_sheets(data: bytes | Path, filename: str | None = None) -> tuple[dict[str, pd.DataFrame], str]:
    """Every sheet of the upload as a raw DataFrame (one `csv`/`parquet` sheet for those formats), and the format."""
    if isinstance(data, Path):
        fname = filename or data.name
        raw_bytes = data.read_bytes()
    else:
        fname = filename or "workbook.xlsx"
        raw_bytes = data
    fmt = validate_upload_bytes(fname, raw_bytes)
    buf = io.BytesIO(raw_bytes)
    if fmt == ".xlsx":
        xl = pd.ExcelFile(buf, engine="openpyxl")
        names = [str(s) for s in xl.sheet_names]
        if not names:
            raise WorkbookValidationError("Excel workbook contains no sheets.")
        if len(names) > MAX_SHEETS:
            raise WorkbookValidationError(f"The workbook has {len(names)} sheets; at most {MAX_SHEETS} are read.")
        sheets = {name: xl.parse(name) for name in names}
    elif fmt == ".csv":
        sheets = {"csv": _read_csv(buf)}
    else:
        sheets = {"parquet": pd.read_parquet(buf)}
    for name, df in sheets.items():
        df.columns = [str(c).lstrip("\ufeff").strip() for c in df.columns]
        _check_size(df, f"Sheet '{name}'" if fmt == ".xlsx" else "Dataset")
    return sheets, fmt


def default_sheet(sheets: dict[str, pd.DataFrame]) -> str:
    """The data sheet: `data` when present, else the sheet with a date column and the most rows."""
    if DEFAULT_DATA_SHEET in sheets:
        return DEFAULT_DATA_SHEET
    names = list(sheets)

    def has_date(df: pd.DataFrame) -> bool:
        return any(column_kind(df[c]) == "date" for c in df.columns[:MAX_COLS])

    dated = [n for n in names if len(sheets[n]) and has_date(sheets[n])]
    pool = dated or names
    return max(pool, key=lambda n: (len(sheets[n]), -names.index(n)))


def read_raw_dataframe(
    data: bytes | Path,
    filename: str | None = None,
    sheet_name: str | None = None,
) -> tuple[pd.DataFrame, list[str], str | None]:
    """Read an uploaded workbook into a raw DataFrame and return (df, sheet_names, selected_sheet)."""
    sheets, _ = read_sheets(data, filename)
    names = list(sheets)
    chosen = sheet_name if (sheet_name and sheet_name in sheets) else default_sheet(sheets)
    df = sheets[chosen]
    if len(df) == 0:
        raise WorkbookValidationError("Uploaded dataset has 0 rows.")
    return df, names, chosen


def _check_rows(df: pd.DataFrame, dates: pd.Series, mapping: Any) -> tuple[pd.DataFrame, int]:
    """Normalise dates to days, drop exact duplicate rows and reject what cannot be repaired without guessing.

    Returns `(clean_df, exact_duplicates_removed)`. Rows that share a date and channel but differ elsewhere, and negative
    costs, raise `WorkbookValidationError`: summing or picking one of them would silently change sales or spend.
    """
    out = df.copy()
    out[mapping.date] = dates
    exact = out.duplicated(keep="first")
    out = out.loc[~exact].reset_index(drop=True)
    key = [mapping.date, mapping.channel]
    conflicts = out.duplicated(subset=key, keep=False)
    if conflicts.any():
        examples = out.loc[conflicts, key].drop_duplicates().head(3)
        sample = "; ".join(f"{pd.Timestamp(d):%Y-%m-%d} · {c}" for d, c in examples.itertuples(index=False))
        raise WorkbookValidationError(
            f"{int(conflicts.sum())} rows share a date and channel but have different values (e.g. {sample}). "
            "Combine or remove them so each channel has one row per day, then upload again."
        )
    costs = pd.to_numeric(out[mapping.costs], errors="coerce")
    negative = costs < 0
    if negative.any():
        examples = out.loc[negative, key].head(3)
        sample = "; ".join(f"{pd.Timestamp(d):%Y-%m-%d} · {c}" for d, c in examples.itertuples(index=False))
        raise WorkbookValidationError(
            f"{int(negative.sum())} rows have a negative cost (e.g. {sample}). Costs must be zero or positive."
        )
    return out, int(exact.sum())


# At or above this prior probability that the media channels earn more than all of the target, Meridian's default ROI
# prior contradicts the data (the PASS bound of the "Prior P(total media contribution > 100 %)" health check)
PRIOR_OVER_100_LIMIT = 0.20
_MEDIA_ROLES = ("paid", "digital_paid", "print_event")


def default_prior_over_100(media_spend: list[float], total_target: float, draws: int = 20_000) -> float | None:
    """P(Σ ROI_c × spend_c > total target) when every media channel's ROI follows Meridian's default prior,
    LogNormal(0.2, 0.9), independently: the prior's chance that media earn more than all sales (a negative baseline).

    Monte Carlo with a fixed seed, so a workbook always gets the same value; None without media spend or target.
    """
    spend = np.array([float(s) for s in media_spend if float(s) > 0], dtype=np.float64)
    if total_target <= 0 or spend.size == 0:
        return None
    rng = np.random.default_rng(0)
    roi = rng.lognormal(MERIDIAN_ROI_PRIOR[0], MERIDIAN_ROI_PRIOR[1], size=(draws, spend.size))
    return float(np.mean(roi @ spend > float(total_target)))


def _roi_prior_default(role_config: DataRoleConfig, total_target: float, explicit: bool) -> tuple[float | None, str | None]:
    """Check the ROI prior against the data's media share; returns `(P(media > 100 %) under the default prior, warning)`.

    When the run has not chosen its ROI prior (`explicit` False) and Meridian's default would put at least
    `PRIOR_OVER_100_LIMIT` on media earning more than all sales, the run starts with the total-media-contribution prior
    (Meridian's guardrail against a negative baseline), and the warning says so. A chosen prior is never replaced; the
    warning then only points at the risk.
    """
    media = [ch.total_cost for ch in role_config.channels if ch.enabled and ch.role in _MEDIA_ROLES]
    prob = default_prior_over_100(media, total_target)
    ms = role_config.model_settings
    if prob is None or prob < PRIOR_OVER_100_LIMIT or ms.media_prior_type != "roi":
        return prob, None
    share = sum(media) / total_target
    risk = (
        f"Media spend is {100 * share:.0f}% of the target, so Meridian's default ROI prior gives a {100 * prob:.0f}% "
        "chance that media earn more than all sales (a negative baseline)."
    )
    if explicit:
        if ms.roi_prior == "meridian":
            return prob, f"{risk} Consider the total-media-contribution ROI prior (run settings, Priors)."
        return prob, None
    ms.roi_prior = "total_media_contribution"
    return prob, (
        f"{risk} The run starts with the total-media-contribution ROI prior instead (media "
        f"{100 * ms.total_media_contribution_mean:.0f}% ± {100 * ms.total_media_contribution_sd:.0f}% of sales); change it "
        "under Priors."
    )


def apply_channel_choices(inferred: list[ChannelConfig], choices: list[ChannelConfig]) -> list[ChannelConfig]:
    """The inferred channels with the user's role (and demand-following / shift) choices applied by name.

    Statistics stay the ones measured on the data. A changed role resets the channel's transforms, as the settings
    form's role picker does. A choice for a channel the data does not have is an error.
    """
    by_name = {c.name: c for c in choices}
    unknown = sorted(set(by_name) - {c.name for c in inferred})
    if unknown:
        raise WorkbookValidationError(f"Channel{'s' if len(unknown) > 1 else ''} not in the data: {', '.join(unknown)}.")
    out: list[ChannelConfig] = []
    for ch in inferred:
        choice = by_name.get(ch.name)
        if choice is None or choice.role == ch.role:
            data = ch.model_dump()
            if choice is not None and choice.demand_following is not None:
                data["demand_following"] = choice.demand_following
            out.append(ChannelConfig.model_validate(data))
            continue
        data = ch.model_dump()
        data.update(
            role=choice.role,
            adstock_shape=None,
            saturation="hill",
            in_home_shift_days=choice.in_home_shift_days if choice.role == "print_event" else None,
            rationale=None,
            cost_column=None,
            exposure_column=None,
            exposure_type=None,
            adstock_decay=None,
        )
        if choice.demand_following is not None:
            data["demand_following"] = choice.demand_following
        out.append(ChannelConfig.model_validate(data))
    return out


def profile_workbook(
    data: bytes | Path,
    filename: str = "sample_data.xlsx",
    role_config: DataRoleConfig | None = None,
    channel_choices: list[ChannelConfig] | None = None,
    resample_report: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, DataProfile]:
    """Read an upload, map it onto the canonical schema, infer or apply the DataRoleConfig, and profile it.

    Without `role_config` the mapping is guessed (`suggest_mapping`). A `role_config` without channels gets them
    inferred from the data, with `channel_choices` (the wizard's roles) applied by name. Its hold-out, when not set
    explicitly, follows the history (`default_holdout_days`), and so does its ROI prior (`_roi_prior_default`). The
    mapping's resampling (`core/resample.py`) brings the rows onto the daily grid; `resample_report`, when given, is
    filled with what it did (the upload wizard's "Resample & fill" step shows it).
    """
    roi_prior_explicit = role_config is not None and "roi_prior" in role_config.model_settings.model_fields_set
    requested_sheet = role_config.sheet_name if role_config else None
    raw, sheets, selected_sheet = read_raw_dataframe(data, filename=filename, sheet_name=requested_sheet)
    if requested_sheet and requested_sheet != selected_sheet:
        raise WorkbookValidationError(f"The workbook has no sheet '{requested_sheet}' (sheets: {', '.join(sheets)}).")

    available_columns = [str(c) for c in raw.columns]
    mapping = role_config.columns if role_config is not None else suggest_mapping(raw)
    canonical, map_warnings, prepared = canonicalise_report(raw, mapping)
    if resample_report is not None:
        resample_report.update(prepared.report())
    std = STANDARD_COLUMNS
    df, duplicate_rows_count = _check_rows(canonical, canonical["date"], std)
    dates = pd.to_datetime(df["date"])

    min_date = dates.min()
    max_date = dates.max()
    full_idx = pd.date_range(min_date, max_date, freq="D")
    calendar_days = len(full_idx)
    min_days = MIN_TRAINING_DAYS + MIN_HOLDOUT_DAYS
    if calendar_days < min_days:
        raise WorkbookValidationError(
            f"The data covers {calendar_days} days ({min_date:%Y-%m-%d} .. {max_date:%Y-%m-%d}); a model needs at least "
            f"{min_days} days: a full year of training plus a hold-out."
        )

    # Identify trailing isolated partial year days (e.g. single 2025-01-01 day)
    year_counts = pd.Series(full_idx.year).value_counts()
    trailing_isolated: list[str] = []
    if len(year_counts) > 1:
        max_year = int(full_idx.max().year)
        if int(year_counts.get(max_year, 0)) <= 7:
            trailing_dates = full_idx[full_idx.year == max_year]
            trailing_isolated = [d.strftime("%Y-%m-%d") for d in trailing_dates]

    if role_config is None:
        role_config = DataRoleConfig(
            sheet_name=selected_sheet,
            columns=mapping,
            channels=infer_channel_configs(df, std),
            drop_trailing_isolated_days=False,
        )
    else:
        role_config = role_config.model_copy(deep=True)
        role_config.sheet_name = selected_sheet
        role_config.columns = mapping
        if not role_config.channels:
            role_config.channels = infer_channel_configs(df, std)
    if mapping.layout == "wide":
        # A wide-table channel without any spend has nothing to model (it has no sales either)
        for ch in role_config.channels:
            if ch.total_cost <= 0 and ch.inferred_role == "unpaid" and ch.role == "unpaid":
                ch.role = ch.inferred_role = "ignored"
                ch.enabled = False
                ch.rationale = "No spend on any day"
    if channel_choices:
        role_config.channels = apply_channel_choices(role_config.channels, channel_choices)

    if role_config.drop_trailing_isolated_days and trailing_isolated:
        modelling_dates = full_idx[~full_idx.isin(pd.to_datetime(trailing_isolated))]
    else:
        modelling_dates = full_idx

    modelling_end = modelling_dates.max().strftime("%Y-%m-%d")
    modelling_days = len(modelling_dates)
    holdout_default = default_holdout_days(modelling_dates)
    if "holdout_days" not in role_config.model_fields_set:
        role_config.holdout_days = holdout_default

    # Missing channel-days across active channels
    active_names = set(role_config.active_channels)
    df_active = df[df["channel"].astype(str).isin(active_names)].copy()
    df_active["date"] = pd.to_datetime(df_active["date"])

    present = (
        df_active.assign(present=1)
        .pivot_table(index="date", columns="channel", values="present", fill_value=0)
        .reindex(full_idx, fill_value=0)
    )
    missing_by_ch: dict[str, int] = {}
    for ch in role_config.active_channels:
        if ch in present.columns:
            miss = int((present[ch] == 0).sum())
            if miss > 0:
                missing_by_ch[ch] = miss

    # TV low-sales anomaly days (< 1 EUR)
    tv_low_sales = 0
    tv_matches = [c for c in role_config.active_channels if c.lower() == "tv"]
    if tv_matches and role_config.has_channel_sales:
        tv_rows = df_active[df_active["channel"] == tv_matches[0]]
        tv_sales = pd.to_numeric(tv_rows["sales_by_channel"], errors="coerce").fillna(0.0)
        tv_low_sales = int((tv_sales < 1.0).sum())

    # Catalog send-out days
    catalog_sendouts = 0
    for ch in role_config.print_event_channels:
        ch_rows = df_active[df_active["channel"] == ch]
        ch_costs = pd.to_numeric(ch_rows["costs_by_channel"], errors="coerce").fillna(0.0)
        catalog_sendouts += int((ch_costs > 0).sum())

    if TARGET_COLUMN in df.columns:
        daily_target = df.groupby("date")[TARGET_COLUMN].first()
        total_sales_sum = float(daily_target.sum())
        negative_values_count = int((daily_target < 0).sum())
    else:
        total_sales_sum = float(pd.to_numeric(df_active["sales_by_channel"], errors="coerce").fillna(0.0).sum())
        raw_sales = pd.to_numeric(df["sales_by_channel"], errors="coerce").fillna(0.0)
        negative_values_count = int((raw_sales < 0).sum())
    total_cost_sum = float(pd.to_numeric(df_active["costs_by_channel"], errors="coerce").fillna(0.0).sum())
    years_present = sorted({int(y) for y in dates.dt.year.dropna().unique()})
    prior_over_100, prior_warning = _roi_prior_default(role_config, total_sales_sum, roi_prior_explicit)

    warnings: list[str] = list(map_warnings)
    if prior_warning:
        warnings.append(prior_warning)
    if duplicate_rows_count > 0:
        warnings.append(f"{duplicate_rows_count} exact duplicate rows (every column identical) were removed.")
    if negative_values_count > 0:
        what = "days have a negative target" if TARGET_COLUMN in df.columns else "rows have negative sales (returns?)"
        warnings.append(f"{negative_values_count} {what}; they lower the daily total sales the model fits.")
    if missing_by_ch:
        miss_str = ", ".join(f"{k}: {v} days" for k, v in missing_by_ch.items())
        warnings.append(f"Missing channel-date rows detected ({miss_str}).")
    if tv_low_sales > 0:
        warnings.append(f"TV channel has {tv_low_sales} present days with near-zero sales (< 1 €).")
    if trailing_isolated:
        if role_config.drop_trailing_isolated_days:
            warnings.append(
                f"Trailing isolated year dates {trailing_isolated} will be excluded from the modelling window."
            )
        else:
            warnings.append(
                f"Trailing isolated year dates {trailing_isolated} stay in the models but are "
                "left out of yearly and monthly comparisons."
            )

    sample_df = raw.head(SAMPLE_ROWS).copy()
    for c in sample_df.columns:
        if pd.api.types.is_datetime64_any_dtype(sample_df[c]):
            sample_df[c] = sample_df[c].dt.strftime("%Y-%m-%d")
    sample_rows = sample_df.where(pd.notna(sample_df), None).to_dict(orient="records")

    profile = DataProfile(
        filename=filename,
        sheet_names=sheets,
        selected_sheet=selected_sheet,
        raw_row_count=len(df) if mapping.layout == "long" else len(raw),
        calendar_days=calendar_days,
        start_date=min_date.strftime("%Y-%m-%d"),
        end_date=max_date.strftime("%Y-%m-%d"),
        modelling_end_date=modelling_end,
        modelling_days=modelling_days,
        trailing_isolated_dates=trailing_isolated,
        available_columns=available_columns,
        sample_rows=sample_rows,
        role_config=role_config,
        missing_channel_days=missing_by_ch,
        tv_low_sales_days=tv_low_sales,
        catalog_sendout_days=catalog_sendouts,
        total_sales_sum=round(total_sales_sum, 2),
        total_cost_sum=round(total_cost_sum, 2),
        years_present=years_present,
        duplicate_rows_count=duplicate_rows_count,
        negative_values_count=negative_values_count,
        warnings=warnings,
        layout=mapping.layout,
        has_channel_sales=role_config.has_channel_sales,
        control_names=[c.name for c in mapping.controls],
        default_holdout_days=holdout_default,
        max_holdout_days=min(365, modelling_days - MIN_TRAINING_DAYS),
        prior_media_over_100_prob=prior_over_100,
        default_roi_prior=(
            "total_media_contribution" if prior_over_100 is not None and prior_over_100 >= PRIOR_OVER_100_LIMIT else "meridian"
        ),
    )
    return df, profile


def inspect_workbook(
    data: bytes,
    filename: str,
    sheet: str | None = None,
    role_config: DataRoleConfig | None = None,
) -> dict[str, Any]:
    """What the upload wizard shows before a run exists: sheets, column profiles, the mapping and a preview profile.

    The mapping is `role_config.columns` when given, else the suggested one (`suggested: true`). When the mapping can be
    applied, `preview` is the DataProfile a run would start with (inferred channel roles, warnings, hold-out bounds);
    otherwise `errors` says why not, and `prep_errors` holds those of them the resampling can fix (the wizard's
    "Resample & fill" step). `time_grid` describes the dates, `resample` what the resampling did (each column's methods
    and counts, the daily series for the preview chart) and `resample_options` every method it offers. Nothing is stored.
    """
    sheets, fmt = read_sheets(data, filename)
    if sheet and sheet not in sheets:
        raise WorkbookValidationError(f"The workbook has no sheet '{sheet}' (sheets: {', '.join(sheets)}).")
    chosen = sheet or (role_config.sheet_name if role_config and role_config.sheet_name in sheets else None) or default_sheet(sheets)
    df = sheets[chosen]
    if len(df) == 0:
        raise WorkbookValidationError(f"Sheet '{chosen}' has no rows.")

    suggested = role_config is None
    mapping = role_config.columns if role_config is not None else suggest_mapping(df)
    errors = mapping_errors(df, mapping)
    prep_errors: list[str] = []
    if not errors:
        prep_errors = resample_errors(df, mapping)
        errors += prep_errors
    preview: DataProfile | None = None
    resample: dict[str, Any] = {}
    if not errors:
        rc = role_config.model_copy(deep=True) if role_config is not None else DataRoleConfig(columns=mapping)
        rc.sheet_name = chosen
        choices = rc.channels
        rc.channels = []
        try:
            _, preview = profile_workbook(data, filename=filename, role_config=rc, channel_choices=choices, resample_report=resample)
        except PreparationError as exc:
            prep_errors.append(str(exc))
            errors.append(str(exc))
            preview = None
        except WorkbookValidationError as exc:
            errors.append(str(exc))
            preview = None
    time_grid: dict[str, Any] | None = None
    if mapping.date in df.columns:
        keys = df[mapping.channel] if mapping.layout == "long" and mapping.channel in df.columns else None
        time_grid = describe_dates(df[mapping.date], keys)
    structural = [e for e in errors if e not in prep_errors]
    choices = column_choices(df, mapping) if not structural else []

    sheet_info = []
    side_tables = []
    for name, frame in sheets.items():
        kinds = [column_kind(frame[c]) for c in frame.columns]
        sheet_info.append(
            {"name": name, "rows": int(len(frame)), "columns": int(frame.shape[1]), "has_date": "date" in kinds}
        )
        if name != chosen and 0 < len(frame) <= SIDE_TABLE_MAX_ROWS:
            side_tables.append(
                {
                    "sheet": name,
                    "columns": [str(c) for c in frame.columns],
                    "rows": json_records(frame.dropna(how="all"), SIDE_TABLE_PREVIEW_ROWS),
                    "n_rows": int(len(frame)),
                }
            )
    return {
        "filename": filename,
        "format": fmt.lstrip("."),
        "sheets": sheet_info,
        "selected_sheet": chosen,
        "n_rows": int(len(df)),
        "columns": profile_columns(df),
        "sample_rows": json_records(df, SAMPLE_ROWS),
        "side_tables": side_tables,
        "mapping": mapping.model_dump(mode="json"),
        "suggested": suggested,
        "errors": errors,
        "prep_errors": prep_errors,
        "time_grid": time_grid,
        "resample": resample or None,
        "resample_columns": choices,
        "resample_options": method_catalog(),
        "preview": preview.model_dump(mode="json") if preview is not None else None,
        "min_days": MIN_TRAINING_DAYS + MIN_HOLDOUT_DAYS,
    }
