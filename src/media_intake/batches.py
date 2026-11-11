"""后台批次：供 MCP 工具和测试调用，不依赖 MCP SDK。"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from media_intake.config import BatchOptions
from media_intake.errors import DependencyError, InputError
from media_intake.library import MANIFEST_NAME, STATE_DIR_NAME, utc_now
from media_intake.models import RunReport
from media_intake.pipeline import run_batch
from media_intake.store import JobStore
from media_intake.transcribe import TranscribeMode, WHISPER_INSTALL_HINT, parse_transcribe_mode, whisper_is_installed

RunBatchFn = Callable[..., RunReport]


def new_batch_id() -> str:
    return "batch-" + uuid.uuid4().hex


class BatchManager:
    """在后台线程里调用 run_batch，并把批次状态写到素材库。"""

    def __init__(self, *, run_batch_fn: RunBatchFn | None = None, runner: Any | None = None) -> None:
        self._run_batch = run_batch_fn or run_batch
        self._runner = runner
        self._lock = threading.Lock()
        self._batches: dict[str, dict[str, Any]] = {}

    def submit_batch(
        self,
        items: list[str],
        library: str,
        *,
        format: str | None = None,
        audio_only: bool = False,
        subs: bool = True,
        sub_langs: str | None = None,
        transcribe: str = "never",
        cookies: str | None = None,
        cookies_from_browser: str | None = None,
        expand: bool = False,
        max_items: int | None = None,
        whisper_model: str = "base",
        whisper_device: str = "auto",
        whisper_compute_type: str = "auto",
        whisper_language: str | None = None,
        concurrency: int = 2,
        retries: int = 3,
        retry_wait: float = 1.0,
        force: bool = False,
    ) -> dict[str, Any]:
        cleaned = [str(item).strip() for item in items if str(item).strip()]
        if not cleaned:
            raise InputError("items 不能为空")
        mode = parse_transcribe_mode(transcribe)
        cookies_path = Path(cookies).expanduser() if cookies else None
        if cookies_path is not None and not cookies_path.is_file():
            raise InputError(f"找不到 cookies 文件：{cookies_path}")
        if mode is not TranscribeMode.NEVER and not whisper_is_installed():
            raise DependencyError(WHISPER_INSTALL_HINT)
        try:
            options = BatchOptions(
                library_root=Path(library),
                concurrency=concurrency,
                retries=retries,
                retry_wait=retry_wait,
                format=format,
                audio_only=audio_only,
                write_subs=subs,
                sub_langs=sub_langs,
                cookies_file=cookies_path,
                cookies_from_browser=cookies_from_browser,
                expand=expand,
                max_items=max_items,
                transcribe_mode=mode,
                whisper_model=whisper_model,
                whisper_device=whisper_device,
                whisper_compute_type=whisper_compute_type,
                whisper_language=whisper_language,
                force=force,
            )
        except ValueError as exc:
            raise InputError(str(exc)) from exc
        batch_id = new_batch_id()
        rec: dict[str, Any] = {
            "batch_id": batch_id,
            "status": "queued",
            "library": str(options.library_root),
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "items": cleaned,
            "item_count": len(cleaned),
            "options": _public_options(options, transcribe=mode.value),
            "counts": {"succeeded": 0, "skipped": 0, "failed": 0, "total": len(cleaned)},
            "jobs": [],
            "error": None,
            "exit_code": None,
        }
        with self._lock:
            self._batches[batch_id] = rec
        _persist_batch(rec)
        thread = threading.Thread(
            target=self._worker,
            args=(batch_id, options),
            name=f"media-intake-{batch_id}",
            daemon=True,
        )
        thread.start()
        return {
            "batch_id": batch_id,
            "status": rec["status"],
            "item_count": rec["item_count"],
            "library": rec["library"],
        }

    def get_status(self, id: str, library: str | None = None) -> dict[str, Any]:
        batch_id = (id or "").strip()
        if not batch_id:
            raise InputError("请提供批次 ID 或条目 ID")
        rec = self._lookup_batch(batch_id, library)
        if rec is not None:
            return _batch_status(rec)
        if not library:
            raise InputError("未找到该批次。查询单条条目时请同时提供 library")
        return _item_status(batch_id, Path(library))

    def read_manifest(
        self,
        library: str,
        *,
        status: str | None = None,
        source_kind: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if not library or not str(library).strip():
            raise InputError("请提供素材库目录 library")
        if limit is not None and limit < 1:
            raise InputError("limit 必须大于等于 1")
        root = Path(library).expanduser().resolve()
        path = root / MANIFEST_NAME
        records: list[dict[str, Any]] = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if status and rec.get("status") != status:
                    continue
                if source_kind and rec.get("source_kind") != source_kind:
                    continue
                records.append(rec)
                if limit is not None and len(records) >= limit:
                    break
        return {"library": str(root), "count": len(records), "records": records}

    def _lookup_batch(self, batch_id: str, library: str | None) -> dict[str, Any] | None:
        with self._lock:
            rec = self._batches.get(batch_id)
            if rec is not None:
                return json.loads(json.dumps(rec))
        if library:
            loaded = _load_batch(Path(library), batch_id)
            if loaded is not None:
                with self._lock:
                    self._batches.setdefault(batch_id, loaded)
                return loaded
        # 进程内已知批次若未传 library，仍可按 ID 查找
        return None

    def _worker(self, batch_id: str, options: BatchOptions) -> None:
        self._update(batch_id, status="running")
        try:
            with self._lock:
                items = list(self._batches[batch_id]["items"])
            kwargs: dict[str, Any] = {}
            if self._runner is not None:
                kwargs["runner"] = self._runner
            report = self._run_batch(items, options, **kwargs)
            jobs = [_job_brief(job) for job in (*report.succeeded, *report.skipped, *report.failed)]
            counts = {
                "succeeded": len(report.succeeded),
                "skipped": len(report.skipped),
                "failed": len(report.failed),
                "total": len(jobs),
            }
            status = "succeeded"
            if report.failed and (report.succeeded or report.skipped):
                status = "completed"
            elif report.failed:
                status = "failed"
            self._update(
                batch_id,
                status=status,
                jobs=jobs,
                counts=counts,
                exit_code=report.exit_code,
                error=None,
            )
        except Exception as exc:
            self._update(batch_id, status="failed", error=f"{type(exc).__name__}: {exc}", exit_code=1)

    def _update(self, batch_id: str, **fields: Any) -> None:
        with self._lock:
            rec = self._batches[batch_id]
            rec.update(fields)
            rec["updated_at"] = utc_now()
            snapshot = json.loads(json.dumps(rec))
        _persist_batch(snapshot)


def _public_options(options: BatchOptions, *, transcribe: str) -> dict[str, Any]:
    return {
        "format": options.format,
        "audio_only": options.audio_only,
        "subs": options.write_subs,
        "sub_langs": options.sub_langs,
        "transcribe": transcribe,
        "cookies": str(options.cookies_file) if options.cookies_file else None,
        "cookies_from_browser": options.cookies_from_browser,
        "expand": options.expand,
        "max_items": options.max_items,
        "whisper_model": options.whisper_model,
        "whisper_device": options.whisper_device,
        "whisper_compute_type": options.whisper_compute_type,
        "whisper_language": options.whisper_language,
        "concurrency": options.concurrency,
        "retries": options.retries,
        "force": options.force,
    }


def _job_brief(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "source": job.source,
        "status": job.status.value if hasattr(job.status, "value") else str(job.status),
        "title": job.title,
        "error": job.error,
    }


def _batch_status(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "batch",
        "batch_id": rec["batch_id"],
        "status": rec["status"],
        "library": rec["library"],
        "item_count": rec["item_count"],
        "counts": rec.get("counts") or {},
        "jobs": rec.get("jobs") or [],
        "error": rec.get("error"),
        "exit_code": rec.get("exit_code"),
        "created_at": rec.get("created_at"),
        "updated_at": rec.get("updated_at"),
    }


def _item_status(job_id: str, library: Path) -> dict[str, Any]:
    root = library.expanduser().resolve()
    store_path = root / STATE_DIR_NAME / "state.sqlite"
    if not store_path.is_file():
        raise InputError(f"素材库里没有状态库，也找不到该条目：{job_id}")
    store = JobStore(store_path)
    try:
        job = store.get(job_id)
    finally:
        store.close()
    if job is None:
        raise InputError(f"找不到条目：{job_id}")
    return {
        "kind": "item",
        "id": job.id,
        "source": job.source,
        "status": job.status.value,
        "title": job.title,
        "error": job.error,
        "library": str(root),
        "item_dir": job.item_dir,
        "media_path": job.media_path,
        "subtitle_path": job.subtitle_path,
        "transcript_path": job.transcript_path,
    }


def _batch_path(library: Path, batch_id: str) -> Path:
    return Path(library).expanduser().resolve() / STATE_DIR_NAME / "batches" / f"{batch_id}.json"


def _persist_batch(rec: dict[str, Any]) -> None:
    path = _batch_path(Path(rec["library"]), rec["batch_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_batch(library: Path, batch_id: str) -> dict[str, Any] | None:
    path = _batch_path(library, batch_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
