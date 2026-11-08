"""The run's own facts that Gemini needs to read any chart correctly: data window, channel roles and validation.

Everything comes from the run's frozen settings, its workbook profile and its stored results, never from fixed text
(channel names, roles, the print shift and the demand-following list are the run's, as in every narrative).
"""

from __future__ import annotations

import math
from typing import Any

from mmm_studio.core.meridian.spec import demand_following_slugs
from mmm_studio.core.schema import extract_role_config
from mmm_studio.db.models import Run
from mmm_studio.storage import StorageBackend

MEDIA_ROLES = ("paid", "digital_paid", "print_event")


def _names(names: list[str]) -> str:
    return ", ".join(names)


def _share(value: Any) -> str | None:
    return f"{float(value) * 100:.1f} %" if isinstance(value, (int, float)) and math.isfinite(value) else None


def _upper_bound_slugs(run: Run, storage: StorageBackend) -> list[str]:
    """The pipeline's own demand-following list (stored with the default optimisation), else the run's settings."""
    key = f"runs/{run.id}/data/default_optimisation.json"
    try:
        if storage.exists(key):
            stored = storage.read_json(key).get("upper_bound_channels")
            if stored is not None:
                return [str(s) for s in stored]
    except Exception:  # an unreadable file falls back to the settings, like the optimiser API
        pass
    return list(demand_following_slugs(extract_role_config(run.config or {})))


def run_facts(run: Run, storage: StorageBackend) -> str:
    """Bullet lines describing the run; empty when nothing is known."""
    rc = extract_role_config(run.config or {})
    profile = run.schema_json or {}
    summary = run.summary or {}
    lines: list[str] = []

    start = profile.get("date_min") or profile.get("start_date")
    end = profile.get("modelling_end_date") or profile.get("date_max") or profile.get("end_date")
    days = profile.get("modelling_days") or profile.get("calendar_days")
    if start and end:
        lines.append(f"Daily data from {start} to {end}" + (f" ({days} days)" if days else "") + "; the target is total daily sales in €.")

    enabled = [c for c in rc.channels if c.enabled]
    media = [c.name for c in enabled if c.role in MEDIA_ROLES]
    if media:
        lines.append(f"Paid media channels (the model estimates an ROI for each): {_names(media)}.")
    upper = set(_upper_bound_slugs(run, storage))
    following = [c.name for c in enabled if c.slug in upper and c.role in MEDIA_ROLES]
    if following:
        lines.append(
            f"Demand-following channels: {_names(following)}. Their cost is billed per click and rises whenever demand "
            "rises, so their ROI is an upper bound rather than proof of a causal effect."
        )
    prints = [c.name for c in enabled if c.role == "print_event"]
    if prints:
        lines.append(
            f"Print channel(s): {_names(prints)}, modelled from the in-home date ({rc.print_shift} days after the "
            "send-out date) with carry-over to the following days."
        )
    commission = [c.name for c in enabled if c.role == "commission"]
    if commission:
        lines.append(f"Commission channel(s) used as controls, without an ROI: {_names(commission)}.")
    controls = [c.name for c in enabled if c.role == "control"]
    if controls:
        lines.append(f"Other controls: {_names(controls)}.")
    unpaid = [c.name for c in enabled if c.role == "unpaid"]
    if unpaid:
        lines.append(f"Channels without spend (their sales count in the total; they are not model inputs): {_names(unpaid)}.")

    if (run.config or {}).get("skip_meridian"):
        lines.append("This run skipped the Meridian model; only the OLS benchmark was fitted.")
    holdout = summary.get("holdout_q4_2024") or {}
    mae = _share(holdout.get("MAE_over_mean"))
    if mae:
        label = summary.get("holdout_label") or "hold-out"
        r2 = holdout.get("R2")
        lines.append(
            f"Validation: the last {rc.holdout_days} days ({label}) were hidden from the fit; there the Meridian model's "
            f"MAE / mean is {mae}" + (f" and R² {float(r2):.3f}" if isinstance(r2, (int, float)) else "") + "."
        )
    forward = _share((summary.get("holdout_q3_2024") or {}).get("MAE_over_mean"))
    if forward:
        lines.append(f"Forward hold-out ({summary.get('forward_label') or 'the window before it'}), trained only on earlier days: MAE / mean {forward}.")
    if mae and summary.get("leak_free"):
        lines.append(
            "Both hold-outs are leak-free: each was forecast as a budget plan made the day before it, by a refit on the "
            "earlier days only; the window's sales, the realised cost of commission and click-billed channels (planned "
            "from history instead), the user's controls and the weather were unseen. The final model behind the ROIs, "
            "the optimiser and the reports was then fitted on every day."
        )
    rhat = summary.get("max_rhat")
    if isinstance(rhat, (int, float)) and math.isfinite(rhat):
        lines.append(f"Largest R-hat {rhat:.3f} (below 1.2 means the sampler converged).")
    return "\n".join(f"- {line}" for line in lines)
