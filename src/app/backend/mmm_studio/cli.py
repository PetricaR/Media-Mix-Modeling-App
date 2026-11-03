"""Command-line interface for MMM Studio (`run`, `api`, `worker`, `migrate`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cmd_run(args: argparse.Namespace) -> int:
    from mmm_studio.pipeline.context import RunContext
    from mmm_studio.pipeline.stages import run_full_pipeline
    from mmm_studio.storage import LocalStorage

    raw_input = args.input or args.workbook
    if not raw_input:
        print("Error: --input (or --workbook) is required.", file=sys.stderr)
        return 1
    input_path = Path(raw_input).resolve()
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        return 1

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    storage = LocalStorage(out_dir)

    if args.profile:
        profile = args.profile
    elif args.smoke:
        profile = "smoke"
    elif args.fast:
        profile = "quick"
    else:
        profile = "full"
    ctx = RunContext(
        run_id=args.run_id,
        storage=storage,
        run_profile=profile,
        seed=args.seed,
        on_stage_start=lambda s: print(f"[mmm-studio] Starting stage: {s}", flush=True),
        on_stage_complete=lambda s: print(f"[mmm-studio] Completed stage: {s}", flush=True),
        on_stage_error=lambda s, e: print(f"[mmm-studio] ERROR in stage {s}: {e}", file=sys.stderr, flush=True),
        on_stage_skipped=lambda s, why: print(f"[mmm-studio] Skipped stage: {s} ({why})", flush=True),
    )
    raw = input_path.read_bytes()
    ctx.cache["workbook_bytes"] = raw
    storage.write_bytes(ctx.key("input/workbook.xlsx"), raw)

    summary = run_full_pipeline(ctx, skip_meridian=args.skip_meridian)
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_migrate(_args: argparse.Namespace) -> int:
    """Apply the Alembic migrations and the Procrastinate schema, then register missing example forecasts."""
    from mmm_studio.db.models import ForecastRun, Run
    from mmm_studio.db.session import get_session_factory, init_db
    from mmm_studio.jobs.app import ensure_procrastinate_schema
    from mmm_studio.jobs.registry import register_default_forecast
    from mmm_studio.storage import get_storage

    init_db(force=True)
    ensure_procrastinate_schema()
    # Runs finished before the Meridian job registered its example forecast got it from a GET request; do it here
    registered = 0
    with get_session_factory()() as db:
        for run in db.query(Run).filter(Run.status == "succeeded").all():
            if db.get(ForecastRun, f"default_{run.id}") is None and register_default_forecast(db, get_storage(), run.id):
                registered += 1
    print(f"Database migrated; registered {registered} example forecast(s).")
    return 0


def _cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "mmm_studio.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _cmd_worker(args: argparse.Namespace) -> int:
    from mmm_studio.jobs.worker import run_worker_loop

    return run_worker_loop(
        queue=args.queue,
        burst=args.burst,
        idle_exit=args.idle_exit,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mmm-studio", description="MMM Studio CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Run the offline MMM pipeline on an Excel workbook")
    p_run.add_argument("--input", default=None, help="Path to input .xlsx workbook")
    p_run.add_argument("--workbook", default=None, help="Alias for --input")
    p_run.add_argument("--out", default="./data", help="Output directory for run artifacts (default: ./data)")
    p_run.add_argument("--run-id", default="offline", help="Run identifier (default: offline)")
    p_run.add_argument(
        "--profile",
        choices=["full", "quick", "smoke"],
        default=None,
        help="Sampling profile: full, quick, or smoke",
    )
    p_run.add_argument("--fast", action="store_true", help="Use Quick MCMC sampling profile")
    p_run.add_argument("--smoke", action="store_true", help="Use tiny Smoke MCMC sampling profile")
    p_run.add_argument("--skip-meridian", action="store_true", help="Run only fast analysis & OLS stages")
    p_run.add_argument("--seed", type=int, default=0, help="Random seed")
    p_run.set_defaults(func=_cmd_run)

    # migrate
    p_mig = sub.add_parser("migrate", help="Apply database migrations and the Procrastinate schema")
    p_mig.set_defaults(func=_cmd_migrate)

    # api
    p_api = sub.add_parser("api", help="Start the FastAPI server")
    p_api.add_argument("--host", default="0.0.0.0")
    p_api.add_argument("--port", type=int, default=8000)
    p_api.add_argument("--reload", action="store_true")
    p_api.set_defaults(func=_cmd_api)

    # worker
    p_wrk = sub.add_parser("worker", help="Start a queue worker")
    p_wrk.add_argument("--queue", default="all", choices=["analysis", "meridian", "interactive", "all"])
    p_wrk.add_argument("--burst", action="store_true", help="Exit as soon as the queue is empty")
    p_wrk.add_argument("--idle-exit", type=int, default=0, help="Exit after N seconds of idle queue (0 = never)")
    p_wrk.set_defaults(func=_cmd_worker)

    parsed = parser.parse_args(argv)
    return parsed.func(parsed)


if __name__ == "__main__":
    raise SystemExit(main())
