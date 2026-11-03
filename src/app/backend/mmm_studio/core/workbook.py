"""Workbook ingestion, safety validation, and profiling for Excel/CSV/Parquet files."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

from mmm_studio.core.model_settings import MIN_TRAINING_DAYS
from mmm_studio.core.schema import (
    DataProfile,
    DataRoleConfig,
    detect_column_mapping,
    infer_channel_configs,
)

MAX_DECOMPRESSED_BYTES = 150 * 1024 * 1024  # 150 MB zip-bomb ceiling
MAX_ROWS = 500_000
MAX_COLS = 100


class WorkbookValidationError(ValueError):
    """Raised when an uploaded file fails safety or schema validation."""


def validate_upload_bytes(filename: str, data: bytes, max_upload_mb: int = 25) -> str:
    """Validate file extension, byte size, and zip-bomb bounds for .xlsx files."""
    if not data:
        raise WorkbookValidationError("Uploaded file is empty.")
    max_bytes = max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise WorkbookValidationError(
            f"Uploaded file exceeds maximum allowed size of {max_upload_mb} MB."
        )

    ext = Path(filename).suffix.lower()
    if ext not in {".xlsx", ".csv", ".parquet"}:
        raise WorkbookValidationError(
            f"Unsupported file format '{ext}'. Upload .xlsx, .csv, or .parquet."
        )

    if ext == ".xlsx":
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

    return ext


def read_raw_dataframe(
    data: bytes | Path,
    filename: str | None = None,
    sheet_name: str | None = None,
) -> tuple[pd.DataFrame, list[str], str | None]:
    """Read an uploaded workbook into a raw DataFrame and return (df, sheet_names, selected_sheet)."""
    if isinstance(data, Path):
        fname = filename or data.name
        raw_bytes = data.read_bytes()
    else:
        fname = filename or "workbook.xlsx"
        raw_bytes = data

    ext = validate_upload_bytes(fname, raw_bytes)
    buf = io.BytesIO(raw_bytes)

    if ext == ".xlsx":
        xl = pd.ExcelFile(buf, engine="openpyxl")
        sheets = [str(s) for s in xl.sheet_names]
        if not sheets:
            raise WorkbookValidationError("Excel workbook contains no sheets.")
        chosen = sheet_name if (sheet_name and sheet_name in sheets) else (
            "use_case_data" if "use_case_data" in sheets else sheets[0]
        )
        df = xl.parse(chosen)
        selected_sheet: str | None = chosen
    elif ext == ".csv":
        df = pd.read_csv(buf)
        sheets = ["csv"]
        selected_sheet = "csv"
    else:
        df = pd.read_parquet(buf)
        sheets = ["parquet"]
        selected_sheet = "parquet"

    if len(df) == 0:
        raise WorkbookValidationError("Uploaded dataset has 0 rows.")
    if len(df) > MAX_ROWS:
        raise WorkbookValidationError(f"Dataset row count ({len(df)}) exceeds limit ({MAX_ROWS}).")
    if df.shape[1] > MAX_COLS:
        raise WorkbookValidationError(f"Dataset column count ({df.shape[1]}) exceeds limit ({MAX_COLS}).")

    return df, sheets, selected_sheet


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


def profile_workbook(
    data: bytes | Path,
    filename: str = "case_study.xlsx",
    role_config: DataRoleConfig | None = None,
) -> tuple[pd.DataFrame, DataProfile]:
    """Inspect a workbook, infer or apply DataRoleConfig, and compute DataProfile."""
    requested_sheet = role_config.sheet_name if role_config else None
    df, sheets, selected_sheet = read_raw_dataframe(data, filename=filename, sheet_name=requested_sheet)

    available_columns = [str(c) for c in df.columns]
    col_mapping = role_config.columns if role_config is not None else detect_column_mapping(available_columns)
    for req_col in (col_mapping.date, col_mapping.channel, col_mapping.sales, col_mapping.costs):
        if req_col not in df.columns:
            raise WorkbookValidationError(f"Required column '{req_col}' not found in {available_columns}.")

    dates = pd.to_datetime(df[col_mapping.date], errors="coerce")
    if dates.isna().any():
        raise WorkbookValidationError(f"Column '{col_mapping.date}' contains unparseable dates.")
    df, duplicate_rows_count = _check_rows(df, dates.dt.normalize(), col_mapping)
    dates = pd.to_datetime(df[col_mapping.date])

    if role_config is None:
        role_config = DataRoleConfig(
            sheet_name=selected_sheet,
            columns=col_mapping,
            channels=infer_channel_configs(df, col_mapping),
            drop_trailing_isolated_days=False,
            holdout_days=93,
        )
    elif not role_config.channels:
        role_config.channels = infer_channel_configs(df, col_mapping)

    min_date = dates.min()
    max_date = dates.max()
    full_idx = pd.date_range(min_date, max_date, freq="D")
    calendar_days = len(full_idx)
    min_days = MIN_TRAINING_DAYS + 14
    if calendar_days < min_days:
        raise WorkbookValidationError(
            f"The workbook covers {calendar_days} days ({min_date:%Y-%m-%d} .. {max_date:%Y-%m-%d}); a model needs at least "
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

    if role_config.drop_trailing_isolated_days and trailing_isolated:
        modelling_dates = full_idx[~full_idx.isin(pd.to_datetime(trailing_isolated))]
    else:
        modelling_dates = full_idx

    modelling_end = modelling_dates.max().strftime("%Y-%m-%d")
    modelling_days = len(modelling_dates)

    # Missing channel-days across active channels
    active_names = set(role_config.active_channels)
    df_active = df[df[col_mapping.channel].astype(str).isin(active_names)].copy()
    df_active[col_mapping.date] = pd.to_datetime(df_active[col_mapping.date])

    present = (
        df_active.assign(present=1)
        .pivot_table(index=col_mapping.date, columns=col_mapping.channel, values="present", fill_value=0)
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
    if tv_matches:
        tv_rows = df_active[df_active[col_mapping.channel] == tv_matches[0]]
        tv_sales = pd.to_numeric(tv_rows[col_mapping.sales], errors="coerce").fillna(0.0)
        tv_low_sales = int((tv_sales < 1.0).sum())

    # Catalog send-out days
    catalog_sendouts = 0
    for ch in role_config.print_event_channels:
        ch_rows = df_active[df_active[col_mapping.channel] == ch]
        ch_costs = pd.to_numeric(ch_rows[col_mapping.costs], errors="coerce").fillna(0.0)
        catalog_sendouts += int((ch_costs > 0).sum())

    total_sales_sum = float(pd.to_numeric(df_active[col_mapping.sales], errors="coerce").fillna(0.0).sum())
    total_cost_sum = float(pd.to_numeric(df_active[col_mapping.costs], errors="coerce").fillna(0.0).sum())
    years_present = sorted({int(y) for y in dates.dt.year.dropna().unique()})
    raw_sales = pd.to_numeric(df[col_mapping.sales], errors="coerce").fillna(0.0)
    negative_values_count = int((raw_sales < 0).sum())

    warnings: list[str] = []
    if duplicate_rows_count > 0:
        warnings.append(f"{duplicate_rows_count} exact duplicate rows (every column identical) were removed.")
    if negative_values_count > 0:
        warnings.append(
            f"{negative_values_count} rows have negative sales (returns?); they lower the daily total sales the model fits."
        )
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
                f"Trailing isolated year dates {trailing_isolated} stay in the models (as in the notebooks) but are "
                "left out of yearly and monthly comparisons."
            )

    sample_df = df.head(8).copy()
    for c in sample_df.columns:
        if pd.api.types.is_datetime64_any_dtype(sample_df[c]):
            sample_df[c] = sample_df[c].dt.strftime("%Y-%m-%d")
    sample_rows = sample_df.where(pd.notna(sample_df), None).to_dict(orient="records")

    profile = DataProfile(
        filename=filename,
        sheet_names=sheets,
        selected_sheet=selected_sheet,
        raw_row_count=len(df),
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
    )
    return df, profile
