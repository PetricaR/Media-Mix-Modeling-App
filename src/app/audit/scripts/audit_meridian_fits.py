"""Meridian fits for the audit: (a) recovery of known ROIs on synthetic data, (b) seed reproducibility, (c) how much the
hold-out score depends on the realised Affiliate commission (a function of the target). Uses the app's own pipeline
functions; the expected values come from the synthetic data-generating process, not from the app."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

SP = Path(sys.argv[1])
SP.mkdir(parents=True, exist_ok=True)
REPO = Path("/Users/radanpetrica/02_PROFESSIONAL/Job_Opportunities/2026_Tchibo/Case-Study-Marketing-Data-Science-Tchibo")

from mmm_studio.core.meridian.analysis import extract_fit_table, extract_roi_table  # noqa: E402
from mmm_studio.core.meridian.fit import fit_meridian_isolated  # noqa: E402
from mmm_studio.core.meridian.spec import MeridianSpecConfig  # noqa: E402
from mmm_studio.core.metrics import regression_metrics  # noqa: E402
from mmm_studio.core.model_settings import FAST_SAMPLING, SMOKE_SAMPLING  # noqa: E402

out: dict[str, object] = {}
rng = np.random.default_rng(42)

# ------------------------------------------------------------------ (a) synthetic DGP in Meridian's model family
n = 730
idx = pd.date_range("2022-01-03", periods=n, freq="D", name="date")
L = 21


def norm_geo_adstock(x: np.ndarray, alpha: float) -> np.ndarray:
    w = alpha ** np.arange(L + 1)
    num = np.convolve(x, w)[: len(x)]
    return num / w.sum()


def hill(x: np.ndarray, ec: float, s: float = 1.0) -> np.ndarray:
    return x**s / (x**s + ec**s)


spend = {
    "sea": np.exp(rng.normal(np.log(100_000), 0.35, n)),
    "display": np.exp(rng.normal(np.log(30_000), 0.5, n)),
    "psm": np.exp(rng.normal(np.log(10_000), 0.6, n)),
}
true_roi = {"sea": 3.0, "display": 0.0, "psm": 1.5}
alpha = {"sea": 0.3, "display": 0.5, "psm": 0.4}
contrib = {}
for ch, x in spend.items():
    ad = norm_geo_adstock(x, alpha[ch])
    h = hill(ad, ec=np.median(ad))
    scale = true_roi[ch] * x.sum() / max(h.sum(), 1e-9)
    contrib[ch] = scale * h
t = np.arange(n)
doy = np.asarray(idx.dayofyear, dtype=float)
weekend = np.asarray(idx.dayofweek >= 5, dtype=float)
base = 1_000_000 * (1 + 0.15 * np.sin(2 * np.pi * doy / 365.25) + 0.05 * weekend) + 150 * t
y = np.asarray(base + sum(contrib.values()) + rng.normal(0, 50_000, n), dtype=float)
wide = pd.DataFrame({"total_sales": y, **{f"cost_{c}": v for c, v in spend.items()}}, index=idx)
true_roi_realised = {c: float(contrib[c].sum() / spend[c].sum()) for c in spend}

cfg = MeridianSpecConfig(
    paid_channels=["SEA", "Display", "PSM"], as_controls=(), adstock_decay={}, print_slugs=(), holdout=None,
    calendar=("weekdays",), n_fourier=2, trend=True,
)
t0 = time.time()
mmm = fit_meridian_isolated(wide, cfg, SP / "synthetic_fast.binpb", sampling=FAST_SAMPLING, seed=1, spawn_child=False)
rt = extract_roi_table(mmm)
out["synthetic"] = {
    "fit_seconds": round(time.time() - t0, 1),
    "true_roi": {k: round(v, 3) for k, v in true_roi_realised.items()},
    "posterior": {
        c: {"mean": round(float(rt.loc[c, "roi"]), 3), "lo": round(float(rt.loc[c, "roi_lo"]), 3), "hi": round(float(rt.loc[c, "roi_hi"]), 3),
            "covered": bool(rt.loc[c, "roi_lo"] <= true_roi_realised[c] <= rt.loc[c, "roi_hi"])}
        for c in spend
    },
    "true_media_share_pct": round(100 * sum(contrib[c].sum() for c in spend) / y.sum(), 2),
    "app_media_share_pct": round(float(rt.loc["All Channels", "pct_of_contribution"]), 2),
}
print(json.dumps(out["synthetic"], indent=2), flush=True)

# ------------------------------------------------------------------ (b) reproducibility with a fixed seed
rois = []
for k in range(2):
    m = fit_meridian_isolated(wide, cfg, SP / f"synthetic_smoke_{k}.binpb", sampling=SMOKE_SAMPLING, seed=7, spawn_child=False)
    rois.append(extract_roi_table(m)["roi"].to_dict())
m3 = fit_meridian_isolated(wide, cfg, SP / "synthetic_smoke_seed8.binpb", sampling=SMOKE_SAMPLING, seed=8, spawn_child=False)
out["reproducibility"] = {
    "seed7_run1": {k: round(v, 6) for k, v in rois[0].items()},
    "seed7_run2": {k: round(v, 6) for k, v in rois[1].items()},
    "max_abs_diff_same_seed": float(max(abs(rois[0][k] - rois[1][k]) for k in rois[0])),
    "seed8": {k: round(v, 6) for k, v in extract_roi_table(m3)["roi"].to_dict().items()},
}
print(json.dumps(out["reproducibility"], indent=2), flush=True)

# ------------------------------------------------------------------ (c) hold-out with / without the realised Affiliate commission
from mmm_studio.core.tables import build_daily_wide_table  # noqa: E402
from mmm_studio.core.workbook import profile_workbook  # noqa: E402

xlsx = REPO / "src/app/backend/tests/fixtures/case_study.xlsx"
df, prof = profile_workbook(xlsx.read_bytes(), filename="case_study.xlsx")
rc = prof.role_config
real = build_daily_wide_table(df, config=rc)
cfg_real = MeridianSpecConfig.from_role_config(rc, wide=real)
res = {}
for name, c in {
    "default (Affiliate commission as control)": cfg_real,
    "Affiliate commission removed": replace(
        cfg_real,
        paid_channels=[p for p in cfg_real.paid_channels if p != "Affiliate"],
        as_controls=tuple(s for s in cfg_real.as_controls if s != "affiliate"),
    ),
}.items():
    t0 = time.time()
    m = fit_meridian_isolated(real, c, SP / f"real_{len(res)}.binpb", sampling=FAST_SAMPLING, seed=0, spawn_child=False)
    fit = extract_fit_table(m, c)
    te = fit[fit["holdout"]]
    res[name] = {
        "holdout": c.holdout,
        "holdout_mae_over_mean_pct": round(100 * regression_metrics(te["actual"], te["expected"])["MAE_over_mean"], 2),
        "seconds": round(time.time() - t0, 1),
        "roi": {k: round(float(v), 3) for k, v in extract_roi_table(m)["roi"].items()},
    }
    print(name, json.dumps(res[name]), flush=True)
out["affiliate_leak"] = res
(SP / "meridian_fit_checks.json").write_text(json.dumps(out, indent=2))
print("DONE")
