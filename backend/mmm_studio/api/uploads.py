"""The upload wizard's routes: inspect a file before a run exists, and ask Gemini how to map and resample it.

`POST /uploads/inspect` returns the sheets, column profiles, the mapping (the one sent, or the app's guess), how the
dates are spaced, what the resampling did and the profile a run would start with (or why the mapping cannot be used).
`POST /uploads/recommendation` asks Gemini to map the sheet, checks every item of its answer against the data
(`explain/mapping.review_mapping`) and caches Gemini's raw answer under `mappings/{sha256}.json`.
`POST /uploads/resample-recommendation` asks Gemini how to resample the mapped sheet and fill its gaps column by column
(`explain/resample.review_resample`), cached under `resampling/{sha256}.json`. None of them stores the file or creates a
run: `POST /runs` does that with the confirmed mapping.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from mmm_studio.api.deps import client_ip, get_app_settings, get_app_storage
from mmm_studio.api.explain import take_slot
from mmm_studio.core.schema import DataRoleConfig
from mmm_studio.core.workbook import WorkbookValidationError, inspect_workbook, read_sheets
from mmm_studio.explain.context import unverified_numbers
from mmm_studio.explain.gemini import ExplainFailed, ExplainUnavailable, generate_json
from mmm_studio.explain.mapping import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    MappingAdvice,
    MappingAdviceFailed,
    advice_text,
    build_mapping_prompt,
    parse_mapping_advice,
    review_mapping,
)
from mmm_studio.explain.resample import PROMPT_VERSION as RESAMPLE_PROMPT_VERSION
from mmm_studio.explain.resample import SYSTEM_INSTRUCTION as RESAMPLE_SYSTEM_INSTRUCTION
from mmm_studio.explain.resample import (
    ResampleAdvice,
    ResampleAdviceFailed,
    build_resample_prompt,
    parse_resample_advice,
    review_resample,
)
from mmm_studio.explain.resample import advice_text as resample_advice_text
from mmm_studio.settings import Settings
from mmm_studio.storage import StorageBackend

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/uploads", tags=["uploads"])
UPLOAD_EXTENSIONS = (".xlsx", ".csv")


def read_upload(file: UploadFile, settings: Settings) -> tuple[str, bytes]:
    """The upload's name and bytes, with the extension and size checked (400 / 413)."""
    filename = file.filename or "workbook.xlsx"
    if not filename.lower().endswith(UPLOAD_EXTENSIONS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload an Excel workbook (.xlsx) or a CSV file (.csv).",
        )
    max_bytes = settings.max_upload_mb * 1024 * 1024
    raw = file.file.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"The file exceeds the {settings.max_upload_mb} MB upload limit.",
        )
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is empty.")
    return filename, raw


def parse_role_config(raw: str | None) -> DataRoleConfig | None:
    """A form field holding a DataRoleConfig as JSON (422 with the field errors when it is invalid)."""
    if raw is None or not raw.strip():
        return None
    try:
        return DataRoleConfig.model_validate_json(raw)
    except ValidationError as exc:
        msgs = "; ".join(str(e.get("msg", "")).removeprefix("Value error, ") for e in exc.errors())
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Invalid mapping: {msgs}") from exc


def _inspect(filename: str, raw: bytes, sheet: str | None, rc: DataRoleConfig | None) -> dict[str, Any]:
    try:
        return inspect_workbook(raw, filename, sheet=sheet, role_config=rc)
    except WorkbookValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Upload %r could not be inspected", filename, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The file could not be read as a table with a header row.",
        ) from exc


@router.post("/inspect")
def inspect_upload(
    file: UploadFile = File(...),
    sheet: str | None = Form(None),
    role_config: str | None = Form(None),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """Sheets, column profiles, the mapping and the profile a run would start with. Nothing is stored.

    A plain (sync) handler, like the run upload: profiling runs in FastAPI's thread pool.
    """
    filename, raw = read_upload(file, settings)
    return _inspect(filename, raw, sheet or None, parse_role_config(role_config))


def mapping_cache_key(model: str, prompt: str) -> str:
    digest = hashlib.sha256()
    for part in (PROMPT_VERSION, model, SYSTEM_INSTRUCTION, prompt):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


@router.post("/recommendation")
async def recommend_mapping(
    request: Request,
    file: UploadFile = File(...),
    sheet: str | None = Form(None),
    settings: Settings = Depends(get_app_settings),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Gemini's mapping of the sheet, every item checked against the data (503 without Gemini)."""
    if not settings.explain_available:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Gemini is not configured on this server.")
    filename, raw = await run_in_threadpool(read_upload, file, settings)
    inspection = await run_in_threadpool(_inspect, filename, raw, sheet or None, None)
    prompt = build_mapping_prompt(inspection)
    key = f"mappings/{mapping_cache_key(settings.gemini_model, prompt)}.json"

    record: dict[str, Any] | None = None
    if await run_in_threadpool(storage.exists, key):
        try:
            record = await run_in_threadpool(storage.read_json, key)
            advice = MappingAdvice.model_validate(record["advice"])
        except Exception:  # an unreadable cache entry is simply regenerated
            logger.warning("Unreadable mapping cache entry %s", key)
            record = None
    cached = record is not None
    if record is None:
        take_slot(client_ip(request, settings), settings.explain_per_minute, "mapping suggestions")
        try:
            text = await run_in_threadpool(generate_json, SYSTEM_INSTRUCTION, prompt, settings, MappingAdvice)
            advice = parse_mapping_advice(text)
        except ExplainUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except (ExplainFailed, MappingAdviceFailed) as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        record = {
            "advice": advice.model_dump(),
            "model": settings.gemini_model,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        try:
            await run_in_threadpool(storage.write_json, key, record)
        except Exception:  # the answer is still useful without the cache
            logger.warning("Could not cache mapping suggestion %s", key)

    def _review() -> Any:
        sheets, _ = read_sheets(raw, filename)
        df = sheets[inspection["selected_sheet"]]
        channels = (inspection.get("preview") or {}).get("role_config", {}).get("channels", [])
        names = [c["name"] for c in channels] if inspection["mapping"].get("layout") == "long" else []
        if advice.layout == "long" and not names:
            col = advice.channel_column.strip()
            names = sorted({str(v) for v in df[col].dropna().unique()}) if col in df.columns else []
        return review_mapping(advice, df, [s["name"] for s in inspection["sheets"]], inspection["selected_sheet"], names)

    checked = await run_in_threadpool(_review)
    return {
        "sheet": inspection["selected_sheet"],
        "summary": advice.summary,
        "mapping": checked.mapping.model_dump(mode="json"),
        "channel_roles": checked.channel_roles,
        "column_notes": checked.column_notes,
        "rejected": checked.rejected,
        "errors": checked.errors,
        "unreviewed": checked.unreviewed,
        "country": checked.country,
        "country_reason": checked.country_reason,
        "better_sheet": checked.better_sheet,
        "sheet_reason": checked.sheet_reason,
        "notes": advice.notes[:4],
        "model": record.get("model", settings.gemini_model),
        "generated_at": record.get("generated_at"),
        "cached": cached,
        "unverified_numbers": unverified_numbers(advice_text(advice), prompt),
    }


def resample_cache_key(model: str, prompt: str) -> str:
    digest = hashlib.sha256()
    for part in (RESAMPLE_PROMPT_VERSION, model, RESAMPLE_SYSTEM_INSTRUCTION, prompt):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


@router.post("/resample-recommendation")
async def recommend_resampling(
    request: Request,
    file: UploadFile = File(...),
    sheet: str | None = Form(None),
    role_config: str | None = Form(None),
    settings: Settings = Depends(get_app_settings),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Gemini's frequency and per-column resampling / gap filling for the mapped sheet, every item checked (503 without
    Gemini; 422 when the mapping itself is not usable yet)."""
    if not settings.explain_available:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Gemini is not configured on this server.")
    rc = parse_role_config(role_config)
    if rc is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Send the mapping (role_config) to resample.")
    filename, raw = await run_in_threadpool(read_upload, file, settings)
    inspection = await run_in_threadpool(_inspect, filename, raw, sheet or None, rc)
    structural = [e for e in inspection["errors"] if e not in inspection["prep_errors"]]
    if structural:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Fix the mapping first: {' '.join(structural)}",
        )
    mapping = rc.columns

    def _prompt() -> tuple[Any, str]:
        from mmm_studio.core.resample import column_diagnostics

        sheets, _ = read_sheets(raw, filename)
        frame = sheets[inspection["selected_sheet"]]
        return frame, build_resample_prompt(inspection, column_diagnostics(frame, mapping), mapping)

    df, prompt = await run_in_threadpool(_prompt)
    key = f"resampling/{resample_cache_key(settings.gemini_model, prompt)}.json"

    record: dict[str, Any] | None = None
    if await run_in_threadpool(storage.exists, key):
        try:
            record = await run_in_threadpool(storage.read_json, key)
            advice = ResampleAdvice.model_validate(record["advice"])
        except Exception:  # an unreadable cache entry is simply regenerated
            logger.warning("Unreadable resampling cache entry %s", key)
            record = None
    cached = record is not None
    if record is None:
        take_slot(client_ip(request, settings), settings.explain_per_minute, "resampling suggestions")
        try:
            text = await run_in_threadpool(generate_json, RESAMPLE_SYSTEM_INSTRUCTION, prompt, settings, ResampleAdvice)
            advice = parse_resample_advice(text)
        except ExplainUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except (ExplainFailed, ResampleAdviceFailed) as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        record = {
            "advice": advice.model_dump(),
            "model": settings.gemini_model,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        try:
            await run_in_threadpool(storage.write_json, key, record)
        except Exception:  # the answer is still useful without the cache
            logger.warning("Could not cache resampling suggestion %s", key)

    checked = await run_in_threadpool(review_resample, advice, df, mapping)
    return {
        "summary": advice.summary,
        "resample": checked.config.model_dump(mode="json"),
        "column_notes": checked.column_notes,
        "rejected": checked.rejected,
        "errors": checked.errors,
        "unreviewed": checked.unreviewed,
        "frequency_reason": checked.frequency_reason,
        "period_label_reason": checked.period_label_reason,
        "missing_dates_reason": checked.missing_dates_reason,
        "notes": advice.notes[:4],
        "model": record.get("model", settings.gemini_model),
        "generated_at": record.get("generated_at"),
        "cached": cached,
        "unverified_numbers": unverified_numbers(resample_advice_text(advice), prompt),
    }
