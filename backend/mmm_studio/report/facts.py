"""Case facts computed from the daily panel, shared by the narrative sections (overview, brief, EDA callouts).

Every number a narrative quotes comes from here or from a model result, never from a literal in the text.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.core.features import complete_years
from mmm_studio.core.features import full_years as calendar_full_years
from mmm_studio.core.schema import DataRoleConfig

# A month-start level shift counts as a regime change when the 90-day mean after it differs by this factor or more
REGIME_RATIO = 1.5


def detect_level_shift(series: pd.Series, window: int = 90, min_side: int = 60) -> tuple[pd.Timestamp, float] | None:
    """The month start where the mean of `series` changes most (90 days after vs before), with the after/before ratio.

    Returns None when no month start has at least `min_side` days on both sides or the largest change is below
    `REGIME_RATIO`. Used to find spend regime changes and commission-rate changes in any workbook.
    """
    s = series.dropna()
    if len(s) < 2 * min_side:
        return None
    idx = pd.DatetimeIndex(s.index)
    best: tuple[pd.Timestamp, float] | None = None
    best_score = 0.0
    for c in pd.date_range(idx.min() + pd.Timedelta(days=min_side), idx.max() - pd.Timedelta(days=min_side), freq="MS"):
        b = float(s.loc[c - pd.Timedelta(days=window) : c - pd.Timedelta(days=1)].mean())
        a = float(s.loc[c : c + pd.Timedelta(days=window - 1)].mean())
        if b > 0 and a > 0 and abs(np.log(a / b)) > best_score:
            best, best_score = (c, a / b), abs(np.log(a / b))
    return best if best is not None and np.exp(best_score) >= REGIME_RATIO else None


def regime_windows(panel: pd.DataFrame, brk: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Days before and after a regime change within its calendar year, or ±90 days when either side
    of the year has fewer than 30 days."""
    yr = brk.year
    before = panel.loc[pd.Timestamp(f"{yr}-01-01") : brk - pd.Timedelta(days=1)]
    after = panel.loc[brk : pd.Timestamp(f"{yr}-12-31")]
    if len(before) < 30 or len(after) < 30:
        before = panel.loc[brk - pd.Timedelta(days=90) : brk - pd.Timedelta(days=1)]
        after = panel.loc[brk : brk + pd.Timedelta(days=89)]
    return before, after


def _corr(a: pd.Series, b: pd.Series) -> float:
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(a.corr(b))


def _sum_ratio(num: pd.Series, den: pd.Series) -> float:
    d = float(den.sum())
    return float(num.sum()) / d if d else float("nan")


def compute_case_facts(panel: pd.DataFrame, config: DataRoleConfig) -> dict[str, Any]:
    """Headline EDA facts on the given panel (full calendar; yearly views use complete years)."""
    slug_map = config.slug_map()
    y = panel["total_sales"]
    idx = pd.DatetimeIndex(panel.index)
    full_years = complete_years(panel)
    years = calendar_full_years(full_years)
    facts: dict[str, Any] = {
        "n_days": int(len(panel)),
        "start": idx.min().strftime("%Y-%m-%d"),
        "end": idx.max().strftime("%Y-%m-%d"),
        "total_sales": float(y.sum()),
        "mean_daily_sales": float(y.mean()),
        "total_spend": float(panel["total_cost"].sum()) if "total_cost" in panel else 0.0,
        "years": years,
        "n_channels": len(config.active_channels),
        "n_paid": len(config.paid_channels),
        "paid_channels": list(config.paid_channels),
        "unpaid_channels": list(config.unpaid_channels),
    }
    trailing = panel[~panel.index.isin(full_years.index)]
    facts["trailing_days"] = [d.strftime("%Y-%m-%d") for d in trailing.index]
    facts["trailing_sales"] = float(trailing["total_sales"].sum()) if len(trailing) else 0.0

    if len(years) >= 2:
        y0, y1 = years[-2], years[-1]
        fy = full_years.index.year
        s0, s1 = float(full_years.loc[fy == y0, "total_sales"].sum()), float(full_years.loc[fy == y1, "total_sales"].sum())
        c0 = float(full_years.loc[fy == y0, "total_cost"].sum()) if "total_cost" in full_years else 0.0
        c1 = float(full_years.loc[fy == y1, "total_cost"].sum()) if "total_cost" in full_years else 0.0
        facts.update(
            yoy_years=(y0, y1),
            sales_by_year={y0: s0, y1: s1},
            yoy_sales_pct=100.0 * (s1 / s0 - 1.0) if s0 else float("nan"),
            yoy_spend_pct=100.0 * (c1 / c0 - 1.0) if c0 else float("nan"),
        )

    # Channel shares and attributed ROAS
    sales_tot = {ch: float(panel[f"sales_{slug_map[ch]}"].sum()) for ch in config.active_channels if f"sales_{slug_map[ch]}" in panel}
    all_sales = sum(sales_tot.values()) or 1.0
    facts["sales_share"] = {ch: 100.0 * v / all_sales for ch, v in sales_tot.items()}
    facts["paid_share_pct"] = sum(facts["sales_share"].get(ch, 0.0) for ch in config.paid_channels)
    spend_tot = {ch: float(panel[f"cost_{slug_map[ch]}"].sum()) for ch in config.paid_channels if f"cost_{slug_map[ch]}" in panel}
    all_spend = sum(spend_tot.values()) or 1.0
    facts["spend_share"] = {ch: 100.0 * v / all_spend for ch, v in spend_tot.items()}
    facts["attributed_roas"] = {ch: sales_tot.get(ch, 0.0) / v for ch, v in spend_tot.items() if v > 0}
    facts["top3_sales_share"] = sum(sorted(facts["sales_share"].values(), reverse=True)[:3])
    facts["top3_channels"] = sorted(facts["sales_share"], key=facts["sales_share"].get, reverse=True)[:3]

    # Catalog timing and halo
    prints = [c for c in config.print_event_channels if f"cost_{slug_map[c]}" in panel]
    if prints:
        cat = prints[0]
        cost = panel[f"cost_{slug_map[cat]}"]
        sends = idx[cost > 0]
        lags = {k: float(y.corr(cost.shift(k))) for k in range(0, 22)}
        best = max(lags, key=lambda k: lags[k])
        dss = panel.get("days_since_catalog")
        facts.update(
            catalog=cat,
            n_sendouts=int(len(sends)),
            sendout_weekdays=pd.Series(sends.day_name()).value_counts().to_dict(),
            catalog_cost_min=float(cost[cost > 0].min()) if len(sends) else 0.0,
            catalog_cost_max=float(cost[cost > 0].max()) if len(sends) else 0.0,
            catalog_cost_mean=float(cost[cost > 0].mean()) if len(sends) else 0.0,
            catalog_lag0_r=lags[0],
            catalog_best_lag=int(best),
            catalog_best_lag_r=lags[best],
        )
        if dss is not None:
            window = (dss >= 1) & (dss <= 7)
            halo: dict[str, float] = {"total_sales": 100.0 * (float(y[window].mean()) / float(y[~window].mean()) - 1.0)}
            for ch in config.active_channels:
                col = f"sales_{slug_map[ch]}"
                if col in panel and ch != cat:
                    out_mean = float(panel.loc[~window, col].mean())
                    if out_mean > 0:
                        halo[ch] = 100.0 * (float(panel.loc[window, col].mean()) / out_mean - 1.0)
            facts["halo_7d"] = halo
            top = sorted(((ch, v) for ch, v in halo.items() if ch != "total_sales" and sales_tot.get(ch, 0) > 0.01 * all_sales), key=lambda t: -t[1])
            facts["halo_top"] = top[:5]
            top25 = y.nlargest(25).index
            facts["top25_after_sendout"] = int(sum(1 for d in top25 if 0 <= (dss.get(d, np.nan) if hasattr(dss, "get") else np.nan) <= 7))

    # Billing links and regime breaks (dates detected from the data, never fixed)
    facts["cost_clicks_r"] = {
        ch: _corr(panel[f"cost_{slug_map[ch]}"], panel[f"clicks_{slug_map[ch]}"])
        for ch in config.paid_channels
        if f"cost_{slug_map[ch]}" in panel and f"clicks_{slug_map[ch]}" in panel
    }
    if "sea" in config.paid_slugs and "cost_sea" in panel and "clicks_sea" in panel:
        facts["sea_cost_clicks_r"] = _corr(panel["cost_sea"], panel["clicks_sea"])
    commission = next((c for c in config.channels if c.enabled and c.role == "commission"), None)
    if commission is not None and f"cost_{commission.slug}" in panel and f"sales_{commission.slug}" in panel:
        cost, sales = panel[f"cost_{commission.slug}"], panel[f"sales_{commission.slug}"]
        facts["commission_channel"] = commission.name
        facts["affiliate_cost_sales_r"] = _corr(cost, sales)
        facts["affiliate_commission_pct"] = 100.0 * _sum_ratio(cost, sales)
        shift = detect_level_shift(cost / sales.where(sales > 0))
        if shift is not None:
            brk = shift[0]
            pre, post = panel.loc[: brk - pd.Timedelta(days=1)], panel.loc[brk:]
            facts["affiliate_commission_pre_pct"] = 100.0 * _sum_ratio(pre[cost.name], pre[sales.name])
            facts["affiliate_commission_post_pct"] = 100.0 * _sum_ratio(post[cost.name], post[sales.name])
            facts["affiliate_break"] = brk.strftime("%Y-%m-%d")
    social = next((c for c in config.channels if c.enabled and c.slug == "social_media" and f"cost_{c.slug}" in panel), None)
    shift = detect_level_shift(panel[f"cost_{social.slug}"]) if social is not None else None
    if social is not None and shift is not None:
        brk = shift[0]
        before, after = regime_windows(panel, brk)

        def _ratio(col: str) -> float:
            b = float(before[col].mean()) if col in before else 0.0
            return float(after[col].mean()) / b if b else float("nan")

        facts.update(
            social_channel=social.name,
            social_break=brk.strftime("%Y-%m-%d"),
            social_spend_ratio=_ratio("cost_social_media"),
            social_clicks_ratio=_ratio("clicks_social_media"),
            social_sales_ratio=_ratio("sales_social_media"),
            social_spend_before=float(before["cost_social_media"].mean()),
            social_spend_after=float(after["cost_social_media"].mean()),
        )

    # Data quality
    missing = {}
    for ch in config.active_channels:
        col = f"missing_{slug_map[ch]}"
        if col in panel and int(panel[col].sum()) > 0:
            missing[ch] = int(panel[col].sum())
    facts["missing_channel_days"] = missing
    if "sales_tv" in panel:
        present = panel.get("missing_tv", pd.Series(0, index=idx)) == 0
        facts["tv_low_days"] = int(((panel["sales_tv"] < 1.0) & present).sum())

    # Weekday profile (all days vs ≥ 7 days after a send-out)
    dow = y.groupby(idx.dayofweek).mean()
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    facts["best_weekday"] = names[int(dow.idxmax())]
    facts["worst_weekday"] = names[int(dow.idxmin())]
    if "days_since_catalog" in panel:
        clean = panel["days_since_catalog"] >= 7
        dow_clean = y[clean].groupby(idx[clean].dayofweek).mean()
        if len(dow_clean) == 7:
            facts["best_weekday_clean"] = names[int(dow_clean.idxmax())]
    return facts


def fmt_ratio(v: float) -> str:
    return f"×{v:.1f}" if v == v else "–"


def join_names(names: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    return names[0] if len(names) == 1 else (", ".join(names[:-1]) + " and " + names[-1] if names else "")


def billing_links(panel: pd.DataFrame, config: DataRoleConfig) -> dict[str, Any]:
    """The channels whose cost follows demand, as every page names them.

    `upper`: `(name, r(cost, own clicks))` of the demand-following media channels (the run's setting, else inferred),
    whose ROI is an upper bound; `commission`: `(name, r(cost, own sales))` of the first commission channel or None.
    """
    from mmm_studio.core.meridian.spec import demand_following_slugs

    names = {c.slug: c.name for c in config.channels}
    upper = []
    for slug in demand_following_slugs(config, panel):
        cost, clicks = f"cost_{slug}", f"clicks_{slug}"
        upper.append((names.get(slug, slug), _corr(panel[cost], panel[clicks]) if cost in panel and clicks in panel else float("nan")))
    commission = None
    ch = next((c for c in config.channels if c.enabled and c.role == "commission"), None)
    if ch is not None:
        cost, sales = f"cost_{ch.slug}", f"sales_{ch.slug}"
        commission = (ch.name, _corr(panel[cost], panel[sales]) if cost in panel and sales in panel else float("nan"))
    return {"upper": upper, "commission": commission}


def billing_sentence(links: dict[str, Any]) -> str:
    """'PSM and SEA are billed per click (r(cost, clicks) = 0.92, 0.96); Affiliate is a commission on its own sales (r = 0.80)'."""
    parts = []
    if links["upper"]:
        names = [n for n, _ in links["upper"]]
        rs = ", ".join(f"{r:.2f}" for _, r in links["upper"] if r == r)
        verb = "is" if len(names) == 1 else "are"
        parts.append(f"{join_names(names)} {verb} billed per click" + (f" (r(cost, clicks) = {rs})" if rs else ""))
    if links["commission"]:
        name, r = links["commission"]
        parts.append(f"{name} is a commission on its own sales" + (f" (r(cost, own sales) = {r:.2f})" if r == r else ""))
    return "; ".join(parts)
