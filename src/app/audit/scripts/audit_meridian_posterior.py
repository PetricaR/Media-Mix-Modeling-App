"""Independent checks of the stored full-profile Meridian model (run_f1e8df83da): identities that must hold for any
correct MMM decomposition, recomputed from Meridian's own posterior rather than from the app's tables."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/radanpetrica/02_PROFESSIONAL/Job_Opportunities/2026_Tchibo/Case-Study-Marketing-Data-Science-Tchibo")
RUN = REPO / ".app_data/runs/run_f1e8df83da"
SP = Path(sys.argv[1])
SP.mkdir(parents=True, exist_ok=True)
model_copy = SP / "meridian_final_copy.binpb"
shutil.copyfile(RUN / "models/meridian_final.binpb", model_copy)

from meridian.analysis import analyzer  # noqa: E402
from meridian.analysis.tensors import DataTensors  # noqa: E402

from mmm_studio.core.meridian.spec import load_meridian_model  # noqa: E402

mmm = load_meridian_model(model_copy)
an = analyzer.Analyzer(mmm)
out: dict[str, object] = {}


def flat(x):
    a = np.asarray(x)
    return a.reshape(-1, *a.shape[2:])


channels = list(mmm.input_data.media_channel.values)
times = pd.to_datetime(mmm.input_data.time.values)
print("channels", channels, "n_times", len(times))

# 1. Additivity: expected = baseline(no media) + sum of per-channel incremental outcomes, day by day
exp = flat(an.expected_outcome(aggregate_times=False)).mean(axis=0).reshape(-1)
inc_t = flat(an.incremental_outcome(aggregate_times=False))  # draws x time x channel
inc_mean = inc_t.mean(axis=0)
zero_media = DataTensors(media=np.zeros_like(np.asarray(mmm.input_data.media.values, dtype=float)))
base = flat(an.expected_outcome(new_data=zero_media, aggregate_times=False)).mean(axis=0).reshape(-1)
resid = exp - (base + inc_mean.sum(axis=-1))
out["additivity_max_abs_eur"] = float(np.abs(resid).max())
out["additivity_rel"] = float(np.abs(resid).max() / exp.mean())

# 2. ROI = incremental outcome / spend, per channel (posterior mean of the ratio; spend is fixed)
roi_tbl = pd.read_parquet(RUN / "data/meridian_roi.parquet")
spend = np.asarray(mmm.input_data.media_spend.values, dtype=float).reshape(-1, len(channels)).sum(axis=0)
inc_total = inc_t.sum(axis=1)  # draws x channel
roi_draws = inc_total / spend
roi_check = {}
for i, ch in enumerate(channels):
    roi_check[ch] = {
        "app_roi": round(float(roi_tbl.loc[ch, "roi"]), 4),
        "recomputed_mean": round(float(roi_draws[:, i].mean()), 4),
        "recomputed_q05": round(float(np.quantile(roi_draws[:, i], 0.05)), 4),
        "recomputed_q95": round(float(np.quantile(roi_draws[:, i], 0.95)), 4),
        "app_lo": round(float(roi_tbl.loc[ch, "roi_lo"]), 4),
        "app_hi": round(float(roi_tbl.loc[ch, "roi_hi"]), 4),
        "p_roi_lt_1": round(float((roi_draws[:, i] < 1).mean()), 3),
    }
out["roi"] = roi_check

# 3. Zero spend in one channel -> that channel's incremental outcome is exactly zero
media0 = np.asarray(mmm.input_data.media.values, dtype=float).copy()
j = channels.index("display")
media0[..., j] = 0.0
inc0 = flat(an.incremental_outcome(new_data=DataTensors(media=media0), aggregate_times=True))
out["zero_spend_display_incremental"] = float(np.abs(inc0[..., j]).max())

# 4. Response curve at 1.0x equals the ROI table's incremental outcome; curves are monotone
rc = json.loads((RUN / "data/response_curves.json").read_text())
mono, at1 = {}, {}
for ch, rows in rc.items():
    df = pd.DataFrame(rows)
    mono[ch] = bool((np.diff(df["mean"].to_numpy()) >= -1e-6).all())
    at1[ch] = round(float(df.loc[(df["multiplier"] - 1).abs().idxmin(), "mean"] / roi_tbl.loc[ch, "incremental_outcome"]), 4)
out["response_curve_monotone"] = mono
out["response_curve_at_1x_over_roi_table_inc"] = at1

# 5. mROI: Meridian's definition is the return on a +1 % spend increase; check against the response curve slope near 1x
mroi_fd = {}
for ch, rows in rc.items():
    df = pd.DataFrame(rows).set_index("multiplier")
    lo, hi = df.loc[0.95], df.loc[1.05]
    mroi_fd[ch] = {"curve_slope_0.95_1.05": round(float((hi["mean"] - lo["mean"]) / (hi["spend"] - lo["spend"])), 3),
                   "app_mroi": round(float(roi_tbl.loc[ch, "mroi"]), 3)}
out["mroi_vs_curve_slope"] = mroi_fd

# 6. Convergence beyond R-hat: effective sample size of the key parameters
import arviz as az  # noqa: E402

post = mmm.inference_data.posterior
ess = {}
for var in ("roi_m", "beta_m", "alpha_m", "ec_m", "slope_m", "sigma", "tau_g", "knot_values"):
    if var in post:
        e = az.ess(post[[var]])[var].values
        ess[var] = round(float(np.min(e)), 1)
out["min_bulk_ess"] = ess
out["n_chains_draws"] = [int(post.sizes["chain"]), int(post.sizes["draw"])]
rh = az.rhat(post[[v for v in ("roi_m", "alpha_m", "ec_m", "sigma") if v in post]])
out["max_rhat_key"] = {k: round(float(np.max(rh[k].values)), 4) for k in rh.data_vars}

# 7. Share-of-contribution denominator: Meridian's pct_of_contribution vs observed total sales
kpi = np.asarray(mmm.input_data.kpi.values, dtype=float).sum()
out["pct_contrib_all_app"] = float(roi_tbl.loc["All Channels", "pct_of_contribution"]) if "All Channels" in roi_tbl.index else None
out["inc_all_over_observed_kpi_pct"] = float(100 * inc_total.sum(axis=1).mean() / kpi)
out["inc_all_over_expected_pct"] = float(100 * inc_total.sum(axis=1).mean() / exp.sum())

# 8. Prior vs posterior ROI (how much the data moved each channel's ROI away from the LogNormal(0.2, 0.9) prior)
if "roi_m" in mmm.inference_data.prior:
    pr = flat(mmm.inference_data.prior["roi_m"].values)
    po = flat(post["roi_m"].values)
    out["roi_prior_vs_posterior"] = {
        ch: {"prior_mean": round(float(pr[:, i].mean()), 3), "post_mean": round(float(po[:, i].mean()), 3),
             "post_sd_over_prior_sd": round(float(po[:, i].std() / pr[:, i].std()), 3)}
        for i, ch in enumerate(channels)
    }

print(json.dumps(out, indent=2))
(SP / "meridian_posterior_checks.json").write_text(json.dumps(out, indent=2))
model_copy.unlink()
