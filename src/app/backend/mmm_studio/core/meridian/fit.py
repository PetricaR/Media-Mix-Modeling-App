"""MCMC prior and posterior sampling for Google Meridian, including spawned child-process isolation."""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from mmm_studio.core.meridian.spec import (
    MeridianSpecConfig,
    build_input_data,
    build_meridian_model,
    build_meridian_table,
    load_meridian_model,
    save_meridian_model,
)

FULL_SAMPLING: dict[str, int] = {
    "n_chains": 4,
    "n_adapt": 1000,
    "n_burnin": 500,
    "n_keep": 1000,
    "n_prior": 500,
}

FAST_SAMPLING: dict[str, int] = {
    "n_chains": 2,
    "n_adapt": 300,
    "n_burnin": 200,
    "n_keep": 200,
    "n_prior": 200,
}

SMOKE_SAMPLING: dict[str, int] = {
    "n_chains": 2,
    "n_adapt": 50,
    "n_burnin": 50,
    "n_keep": 50,
    "n_prior": 50,
}


def fit_meridian(
    mmm: Any,
    n_chains: int = 4,
    n_adapt: int = 1000,
    n_burnin: int = 500,
    n_keep: int = 1000,
    n_prior: int = 500,
    seed: int = 0,
) -> Any:
    """Sample prior and posterior on a Meridian instance in-process."""
    mmm.sample_prior(n_prior, seed=seed)
    mmm.sample_posterior(
        n_chains=n_chains,
        n_adapt=n_adapt,
        n_burnin=n_burnin,
        n_keep=n_keep,
        seed=seed,
    )
    return mmm


def _child_fit_worker(
    wide_parquet_path: str,
    cfg: MeridianSpecConfig,
    output_binpb_path: str,
    sampling: dict[str, int],
    seed: int,
    error_file: str,
) -> None:
    """Entry point executed inside a spawned child process for clean TF/JAX isolation."""
    try:
        wide_df = pd.read_parquet(wide_parquet_path)
        table = build_meridian_table(
            wide_df,
            catalog_shift=cfg.catalog_shift,
            n_fourier=cfg.n_fourier,
            paid_channels=cfg.paid_channels,
        )
        input_data = build_input_data(table, cfg)
        mmm = build_meridian_model(input_data, cfg)
        fit_meridian(mmm, seed=seed, **sampling)
        save_meridian_model(mmm, output_binpb_path)
    except Exception as exc:  # noqa: BLE001
        Path(error_file).write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
        raise


def fit_meridian_isolated(
    wide_df: pd.DataFrame,
    cfg: MeridianSpecConfig,
    output_binpb_path: Path | str,
    sampling: dict[str, int] | None = None,
    seed: int = 0,
    spawn_child: bool = True,
    timeout_seconds: int = 1800,
    should_cancel: Callable[[], bool] | None = None,
    on_heartbeat: Callable[[], None] | None = None,
) -> Any:
    """Fit a Meridian model, optionally inside an isolated `spawn` child process.

    When `spawn_child=True`, TensorFlow / JAX memory is confined to the child process
    and reclaimed immediately upon exit, and user cancellation or timeout terminates
    the child cleanly via `proc.terminate()`.
    """
    samp = sampling or FULL_SAMPLING
    out_path = Path(output_binpb_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not spawn_child:
        table = build_meridian_table(
            wide_df,
            catalog_shift=cfg.catalog_shift,
            n_fourier=cfg.n_fourier,
            paid_channels=cfg.paid_channels,
        )
        input_data = build_input_data(table, cfg)
        mmm = build_meridian_model(input_data, cfg)
        fit_meridian(mmm, seed=seed, **samp)
        save_meridian_model(mmm, out_path)
        return mmm

    tmp_wide = out_path.with_suffix(".wide_input.parquet")
    err_path = out_path.with_suffix(".fit_error.txt")
    if err_path.exists():
        err_path.unlink()
    wide_df.to_parquet(tmp_wide)

    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(
        target=_child_fit_worker,
        args=(str(tmp_wide), cfg, str(out_path), samp, seed, str(err_path)),
    )
    proc.start()
    t0 = time.monotonic()
    last_hb = t0

    try:
        while proc.is_alive():
            proc.join(timeout=1.0)
            now = time.monotonic()
            if on_heartbeat and (now - last_hb) >= 10.0:
                on_heartbeat()
                last_hb = now
            if should_cancel and should_cancel():
                proc.terminate()
                proc.join(timeout=5.0)
                if proc.is_alive():
                    proc.kill()
                raise InterruptedError("Meridian MCMC fit cancelled by user.")
            if (now - t0) > timeout_seconds:
                proc.terminate()
                proc.join(timeout=5.0)
                if proc.is_alive():
                    proc.kill()
                raise TimeoutError(f"Meridian MCMC fit exceeded {timeout_seconds}s timeout.")

        if proc.exitcode != 0:
            err_msg = err_path.read_text(encoding="utf-8") if err_path.exists() else f"exit code {proc.exitcode}"
            raise RuntimeError(f"Meridian child process failed: {err_msg}")
    finally:
        if tmp_wide.exists():
            tmp_wide.unlink()
        if err_path.exists():
            err_path.unlink()

    return load_meridian_model(out_path)
