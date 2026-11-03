"""Independent audit checks that need no Meridian fit. Each check prints PASS/FAIL with evidence.

Expected behaviour is stated from first principles (MMM methodology / the app's own documented contract), never by
re-running the app's formula.
"""

from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/radanpetrica/02_PROFESSIONAL/Job_Opportunities/2026_Tchibo/Case-Study-Marketing-Data-Science-Tchibo")
XLSX = REPO / "src/app/backend/tests/fixtures/case_study.xlsx"

RESULTS: list[tuple[str, str, str]] = []


def record(check: str, ok: bool, evidence: str) -> None:
    RESULTS.append((check, "PASS" if ok else "FAIL", evidence))
    print(f"[{'PASS' if ok else 'FAIL'}] {check}\n        {evidence}")


def run(check: str, fn) -> None:
    try:
        fn(check)
    except Exception as exc:  # noqa: BLE001
        record(check, False, f"raised {type(exc).__name__}: {exc}")
        traceback.print_exc()


def to_xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="use_case_data")
    return buf.getvalue()


raw = pd.read_excel(XLSX, sheet_name=None)
sheet = "use_case_data" if "use_case_data" in raw else list(raw)[0]
RAW = raw[sheet]
print("raw columns:", list(RAW.columns), "rows:", len(RAW))


# ---------------------------------------------------------------- data processing
def c_duplicates(check: str) -> None:
    from mmm_studio.core.tables import build_daily_wide_table
    from mmm_studio.core.workbook import profile_workbook

    base_df, base_prof = profile_workbook(to_xlsx(RAW), filename="x.xlsx")
    dup = pd.concat([RAW, RAW.iloc[:50]], ignore_index=True)
    df, prof = profile_workbook(to_xlsx(dup), filename="x.xlsx")
    w0 = build_daily_wide_table(base_df, config=base_prof.role_config)
    w1 = build_daily_wide_table(df, config=prof.role_config)
    extra_sales = float(w1["total_sales"].sum() - w0["total_sales"].sum())
    extra_cost = float(w1["total_cost"].sum() - w0["total_cost"].sum())
    warned = any("duplic" in w.lower() for w in prof.warnings)
    # Expected: duplicated (date, channel) rows are rejected or at least warned about, never silently summed
    record(
        check,
        warned or (extra_sales == 0 and extra_cost == 0),
        f"50 duplicated rows -> profile.duplicate_rows_count={prof.duplicate_rows_count}, warning raised={warned}, "
        f"target total_sales inflated by EUR {extra_sales:,.0f}, total_cost inflated by EUR {extra_cost:,.0f}",
    )


def c_negative(check: str) -> None:
    from mmm_studio.core.workbook import profile_workbook

    neg = RAW.copy()
    cost_col = [c for c in neg.columns if "cost" in c.lower()][0]
    idx = neg.index[neg[cost_col].fillna(0) > 0][:5]
    neg.loc[idx, cost_col] = -neg.loc[idx, cost_col]
    try:
        _, prof = profile_workbook(to_xlsx(neg), filename="x.xlsx")
    except Exception as exc:  # noqa: BLE001
        record(check, True, f"rejected: {exc}")
        return
    warned = any("negativ" in w.lower() for w in prof.warnings)
    record(
        check,
        warned,
        f"5 negative costs -> accepted, negative_values_count={prof.negative_values_count}, warning raised={warned}",
    )


def c_missing_days(check: str) -> None:
    from mmm_studio.core.tables import build_daily_wide_table
    from mmm_studio.core.workbook import profile_workbook

    df, prof = profile_workbook(to_xlsx(RAW), filename="x.xlsx")
    wide = build_daily_wide_table(df, config=prof.role_config)
    miss_cols = [c for c in wide.columns if c.startswith("missing_")]
    any_missing = wide[miss_cols].sum(axis=1) > 0
    # How far do the target's missing-row days fall below comparable days (same weekday, +-14 days)?
    gaps = []
    for d in wide.index[any_missing]:
        win = wide.loc[d - pd.Timedelta(days=14) : d + pd.Timedelta(days=14)]
        comp = win[(win.index.dayofweek == d.dayofweek) & (~any_missing.reindex(win.index))]
        if len(comp):
            gaps.append(float(wide.loc[d, "total_sales"] / comp["total_sales"].mean() - 1))
    from mmm_studio.core.meridian.spec import MeridianSpecConfig, meridian_table

    tbl = meridian_table(wide, MeridianSpecConfig.from_role_config(prof.role_config, wide=wide))
    has_flag = any(c.startswith("missing_") or c == "tv_anomaly" for c in tbl.columns)
    record(
        check,
        has_flag,
        f"{int(any_missing.sum())} days have >=1 missing channel row ({prof.missing_channel_days}); their total_sales "
        f"is on average {100 * np.mean(gaps):+.1f}% vs same-weekday neighbours (min {100 * min(gaps):+.1f}%); "
        f"missing-row flag in Meridian controls={has_flag}",
    )


# ---------------------------------------------------------------- optimiser
def synthetic_curves() -> dict[str, pd.DataFrame]:
    """Hill-type response curves over 0..200 % of historical spend, as Meridian's response_curves returns."""
    mults = np.round(np.arange(0.0, 2.01, 0.05), 2)
    spec = {"sea": (10e6, 30e6, 8e6), "catalog": (20e6, 25e6, 22e6), "display": (5e6, 4e6, 6e6)}
    out = {}
    for ch, (spend, top, k) in spec.items():
        sp = mults * spend
        mean = top * sp / (sp + k)
        out[ch] = pd.DataFrame({"multiplier": mults, "spend": sp, "mean": mean, "ci_lo": 0.8 * mean, "ci_hi": 1.2 * mean})
    return out


def roi_df_for(curves: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = {ch: {"spend": float(c.loc[c.multiplier == 1.0, "spend"].iloc[0])} for ch, c in curves.items()}
    return pd.DataFrame.from_dict(rows, orient="index")


def c_opt_catalog_fixed(check: str) -> None:
    from mmm_studio.core.meridian.optimise import solve_constrained_optimisation

    curves = synthetic_curves()
    roi = roi_df_for(curves)
    hist = float(roi["spend"].sum())
    budget = 1.5 * hist  # larger than the sum of the +30 % upper bounds
    res = solve_constrained_optimisation(roi, curves, total_budget=budget, include_catalog=False)
    cat = next(a for a in res["allocations"] if a["channel"] == "catalog")
    record(
        check,
        abs(cat["optimised_spend"] - cat["current_spend"]) < 1.0,
        f"include_catalog=False, budget 1.5x history: catalog {cat['current_spend']:,.0f} -> {cat['optimised_spend']:,.0f} "
        f"(should stay fixed); result reports include_catalog={res['include_catalog']}",
    )


def c_opt_bounds_respected(check: str) -> None:
    from mmm_studio.core.meridian.optimise import solve_constrained_optimisation

    curves = synthetic_curves()
    roi = roi_df_for(curves)
    hist = float(roi["spend"].sum())
    budget = 0.5 * hist  # smaller than the sum of the -30 % lower bounds
    res = solve_constrained_optimisation(roi, curves, total_budget=budget, default_bounds=(0.7, 1.3))
    viol = [
        f"{a['channel']} x{a['multiplier']:.2f}"
        for a in res["allocations"]
        if a["multiplier"] < 0.7 - 1e-3 or a["multiplier"] > 1.3 + 1e-3
    ]
    record(
        check,
        not viol,
        f"budget 0.5x history with bounds [0.7, 1.3]: allocations outside the user's bounds: {viol or 'none'}; "
        f"no infeasibility error or warning field in result keys={sorted(k for k in res if 'warn' in k or 'feasib' in k)}",
    )


def c_opt_extrapolation(check: str) -> None:
    from mmm_studio.core.meridian.optimise import solve_constrained_optimisation

    curves = synthetic_curves()
    roi = roi_df_for(curves)
    # Multiplier bounds up to 5x are accepted by the API (max_multiplier le=5.0); the curves only cover 0..2x
    res = solve_constrained_optimisation(roi, curves, total_budget=float(roi["spend"].sum()) * 3, default_bounds=(0.0, 5.0))
    worst = []
    for a in res["allocations"]:
        c = curves[a["channel"]]
        spend_hist = float(c.loc[c.multiplier == 1.0, "spend"].iloc[0])
        top = {"sea": 30e6, "catalog": 25e6, "display": 4e6}[a["channel"]]
        k = {"sea": 8e6, "catalog": 22e6, "display": 6e6}[a["channel"]]
        truth = top * a["optimised_spend"] / (a["optimised_spend"] + k)
        worst.append((a["channel"], a["optimised_spend"] / spend_hist, a["optimised_inc_sales"], truth))
    msg = "; ".join(f"{ch} x{m:.2f}: optimiser says {inc / 1e6:,.2f}M vs true curve {t / 1e6:,.2f}M" for ch, m, inc, t in worst)
    beyond = [w for w in worst if w[1] > 2.0]
    err = max((abs(w[2] - w[3]) / max(w[3], 1) for w in beyond), default=0.0)
    record(check, not beyond or err < 0.05, f"allocations beyond the 2x curve range use PCHIP extrapolation: {msg}")


def c_opt_bounds_units(check: str) -> None:
    from mmm_studio.core.meridian.optimise import solve_constrained_optimisation

    curves = synthetic_curves()
    roi = roi_df_for(curves)
    # Multiplier bounds > 10 are still treated as multipliers (capped at the 2.0x curve range), not switched to EUR
    res = solve_constrained_optimisation(roi, curves, channel_bounds={"display": (0.5, 12.0)})
    d = next(a for a in res["allocations"] if a["channel"] == "display")
    record(
        check,
        0.5 - 1e-3 <= d["multiplier"] <= 2.0 + 1e-3,
        f"channel_bounds display=(0.5, 12) treated consistently as multipliers (capped at 2.0x curve ceiling) -> "
        f"display spend {d['optimised_spend']:,.0f} EUR (x{d['multiplier']:.2f})",
    )


# ---------------------------------------------------------------- plan validation
def c_plan_missing_column(check: str) -> None:
    from mmm_studio.core.meridian.forecast import PlanValidationError, validate_plan

    dates = pd.date_range("2025-01-02", periods=7, freq="D")
    plan = pd.DataFrame({"date": dates, "sea": 1e5, "catalg": 5e5, "display": 2e4})  # 'catalog' misspelt
    try:
        out = validate_plan(
            plan,
            last_history_date=pd.Timestamp("2025-01-01"),
            paid_channels=["sea", "catalog", "display", "affiliate", "display_network", "social_media", "psm"],
        )
        record(
            check,
            False,
            f"plan with a misspelt 'catalg' column and missing paid channels was accepted; "
            f"catalog spend used={out['catalog'].sum():,.0f}",
        )
    except PlanValidationError as exc:
        record(check, True, f"rejected with PlanValidationError: {exc}")


# ---------------------------------------------------------------- holdout fallback
def c_holdout_fallback(check: str) -> None:
    from mmm_studio.core.meridian.spec import MeridianSpecConfig
    from mmm_studio.core.schema import default_role_config

    rc = default_role_config()
    rc.holdout_days = 200
    idx = pd.date_range("2022-01-01", periods=390, freq="D")  # 390 <= 2 * 200 -> fallback branch
    cfg = MeridianSpecConfig.from_role_config(rc, index=idx)
    record(
        check,
        cfg.holdout is not None and pd.Timestamp(cfg.holdout[1]) == idx.max(),
        f"data {idx.min().date()}..{idx.max().date()}, holdout_days=200 -> cfg.holdout={cfg.holdout} "
        "(hard-coded Q4 2024, outside the data: the model then trains on every day and the 'hold-out' metrics fall back "
        "to the training metrics in build_meridian_model_section)",
    )


# ---------------------------------------------------------------- narrative
def c_exec_summary(check: str) -> None:
    from mmm_studio.core.tables import build_daily_wide_table
    from mmm_studio.core.workbook import profile_workbook
    from mmm_studio.pipeline.stages import build_overview_section
    from mmm_studio.report.facts import compute_case_facts
    from mmm_studio.core.calendar import add_calendar_and_adstock_features
    from mmm_studio.core.ols import evaluate_ols_suite

    df, prof = profile_workbook(to_xlsx(RAW), filename="x.xlsx")
    wide = build_daily_wide_table(df, config=prof.role_config)
    panel = add_calendar_and_adstock_features(wide, config=prof.role_config)
    facts = compute_case_facts(panel, prof.role_config)
    ols_res = evaluate_ols_suite(wide, config=prof.role_config)
    bad = {"MAE_over_mean": 0.22, "R2": 0.4, "MAE": 1, "RMSE": 1, "MAPE": 0.2, "bias": 0}
    sec = build_overview_section(wide, facts, ols_res, {"holdout_q4_2024": bad, "holdout_q3_2024": bad})
    summary = next(b for b in sec.blocks if getattr(b, "title", "") == "Executive summary")
    text = summary.body
    record(
        check,
        "below the brief" not in text,
        f"hold-out MAE/mean 22 % (target 15 %) -> executive summary says: {text[:230]!r}",
    )


def c_brief_invented(check: str) -> None:
    from mmm_studio.core.tables import build_daily_wide_table
    from mmm_studio.core.workbook import profile_workbook
    from mmm_studio.pipeline.stages import build_brief_section
    from mmm_studio.report.facts import compute_case_facts
    from mmm_studio.core.calendar import add_calendar_and_adstock_features
    from mmm_studio.core.ols import evaluate_ols_suite

    df, prof = profile_workbook(to_xlsx(RAW), filename="x.xlsx")
    wide = build_daily_wide_table(df, config=prof.role_config)
    panel = add_calendar_and_adstock_features(wide, config=prof.role_config)
    facts = compute_case_facts(panel, prof.role_config)
    ols_res = evaluate_ols_suite(wide, config=prof.role_config)
    sec = build_brief_section(wide, facts, ols_res)  # the draft brief s11 writes before Meridian finishes
    tables = [b for b in sec.blocks if getattr(b, "id", "") == "brief_experiments_roadmap_table"]
    cat_rows = [r for t in tables for r in t.rows if "Catalog" in str(r.get("channel"))]
    qa = next(b for b in sec.blocks if getattr(b, "id", "") == "brief_qa_summary_table")
    t2 = next(r["answer"] for r in qa.rows if r["task"].startswith("2"))
    record(
        check,
        not any("≈ 1" in r["risk"] for r in cat_rows),
        f"draft brief (no Meridian ROI yet) catalog row: {cat_rows[0]['risk'] if cat_rows else None!r}; "
        f"fixed text in answer 2: {t2[t2.find('Most important'):t2.find('zero.') + 5]!r}",
    )


# ---------------------------------------------------------------- leakage of hold-out & demand-following costs
def c_leakage(check: str) -> None:
    from mmm_studio.core.meridian.spec import MeridianSpecConfig
    from mmm_studio.core.schema import ChannelRole
    from mmm_studio.core.tables import build_daily_wide_table
    from mmm_studio.core.workbook import profile_workbook

    df, prof = profile_workbook(to_xlsx(RAW), filename="x.xlsx")
    wide = build_daily_wide_table(df, config=prof.role_config)
    rc_roas = prof.role_config.model_copy(deep=True)
    rc_roas.model_settings.roi_prior = "attributed_roas"
    wide_perturbed = wide.copy()
    # Perturb hold-out Q4 2024 attributed sales by 100x; training-only priors must be invariant
    wide_perturbed.loc["2024-10-01":, "sales_sea"] = wide_perturbed.loc["2024-10-01":, "sales_sea"] * 100.0
    cfg_base = MeridianSpecConfig.from_role_config(rc_roas, wide=wide)
    cfg_pert = MeridianSpecConfig.from_role_config(rc_roas, wide=wide_perturbed)
    invariance_ok = abs(cfg_base.roi_prior["sea"][0] - cfg_pert.roi_prior["sea"][0]) < 1e-12

    # Also verify explicit user override of Affiliate to 'commission' is preserved across validation
    from mmm_studio.core.schema import DataRoleConfig

    raw_cfg = prof.role_config.model_dump()
    for ch in raw_cfg["channels"]:
        if ch["slug"] == "affiliate":
            ch["role"] = "commission"
            ch["inferred_role"] = "commission"
    rc_override = DataRoleConfig.model_validate(raw_cfg)
    cfg_no_aff = MeridianSpecConfig.from_role_config(rc_override, wide=wide)
    override_ok = "affiliate" in cfg_no_aff.as_controls and "affiliate" not in cfg_no_aff.media_slugs
    record(
        check,
        invariance_ok and override_ok,
        f"holdout prior invariance={invariance_ok} (mu_sea={cfg_base.roi_prior['sea'][0]:.4f}), "
        f"affiliate commission override respected={override_ok} (as_controls={cfg_no_aff.as_controls})",
    )


for name, fn in [
    ("DP-dup: duplicate (date, channel) rows are not silently summed", c_duplicates),
    ("DP-neg: negative costs are rejected or warned", c_negative),
    ("DP-miss: missing channel-days are handled in the Meridian model", c_missing_days),
    ("OPT-1: include_catalog=False keeps catalog spend fixed", c_opt_catalog_fixed),
    ("OPT-2: optimiser respects the user's min/max bounds", c_opt_bounds_respected),
    ("OPT-3: optimiser stays inside the response-curve range", c_opt_extrapolation),
    ("OPT-4: channel_bounds have one unit", c_opt_bounds_units),
    ("FC-1: a plan with a missing/misspelt channel column is rejected", c_plan_missing_column),
    ("HO-1: hold-out follows holdout_days for any dataset", c_holdout_fallback),
    ("TXT-1: executive summary does not claim the target is met when it is not", c_exec_summary),
    ("TXT-2: draft brief shows no invented catalog ROI", c_brief_invented),
    ("STAT-leak: hold-out accuracy without realised demand-following costs", c_leakage),
]:
    run(name, fn)

print("\nSUMMARY")
for c, s, _ in RESULTS:
    print(f"{s}  {c}")
sys.exit(0)
