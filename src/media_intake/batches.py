"""后台批次：供 MCP 工具和测试调用，不依赖 MCP SDK。"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from media_intake.batch_store import BatchStore
from media_intake.config import BatchOptions
from media_intake.errors import DependencyError, InputError
from media_intake.library import MANIFEST_NAME, STATE_DIR_NAME, Library, utc_now
from media_intake.metadata import get_metadata as lookup_metadata
from media_intake.models import RunReport
from media_intake.pipeline import run_batch
from media_intake.query import read_item as lookup_item
from media_intake.query import read_transcript as lookup_transcript
from media_intake.query import search_library as lookup_search
from media_intake.store import JobStore
from media_intake.transcribe import TranscribeMode, WHISPER_INSTALL_HINT, parse_transcribe_mode, whisper_is_installed

RunBatchFn = Callable[..., RunReport]


def new_batch_id() -> str:
    return "batch-" + uuid.uuid4().hex


class BatchManager:
    """提交并查询批次。默认仍可在当前进程的后台线程里执行；detach=True 时只入队，交给 worker 领取。"""

    def __init__(
        self,
        *,
        run_batch_fn: RunBatchFn | None = None,
        runner: Any | None = None,
        detach: bool = False,
    ) -> None:
        self._run_batch = run_batch_fn or run_batch
        self._runner = runner
        self._detach_default = detach
        self._lock = threading.Lock()
        self._batches: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}

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
        write_auto_subs: bool = False,
        extra_ytdlp_args: list[str] | None = None,
        reference_local: bool = False,
        asr_backend: str = "faster-whisper",
        diarize: bool = False,
        detach: bool | None = None,
    ) -> dict[str, Any]:
        cleaned = [str(item).strip() for item in items if str(item).strip()]
        if not cleaned:
            raise InputError("items 不能为空")
        mode = parse_transcribe_mode(transcribe)
        cookies_path = Path(cookies).expanduser() if cookies else None
        if cookies_path is not None and not cookies_path.is_file():
            raise InputError(f"找不到 cookies 文件：{cookies_path}")
        if mode is not TranscribeMode.NEVER:
            _require_transcriber(asr_backend)
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
                write_auto_subs=write_auto_subs,
                cookies_file=cookies_path,
                cookies_from_browser=cookies_from_browser,
                extra_ytdlp_args=list(extra_ytdlp_args or []),
                expand=expand,
                max_items=max_items,
                transcribe_mode=mode,
                whisper_model=whisper_model,
                whisper_device=whisper_device,
                whisper_compute_type=whisper_compute_type,
                whisper_language=whisper_language,
                force=force,
                reference_local=reference_local,
                asr_backend=asr_backend,
                diarize=diarize,
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
            "counts": {"succeeded": 0, "skipped": 0, "failed": 0, "cancelled": 0, "total": len(cleaned)},
            "jobs": [],
            "error": None,
            "exit_code": None,
            "cancel_requested": False,
            "claimed_by": None,
            "claimed_at": None,
            "heartbeat_at": None,
        }
        with self._lock:
            self._batches[batch_id] = rec
        persist_record(rec)
        use_detach = self._detach_default if detach is None else detach
        if not use_detach:
            event = threading.Event()
            with self._lock:
                self._cancel_events[batch_id] = event
            options.cancel_event = event
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
            "detach": use_detach,
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

    def cancel_batch(self, id: str, library: str | None = None) -> dict[str, Any]:
        batch_id = (id or "").strip()
        if not batch_id:
            raise InputError("请提供批次 ID")
        rec = self._lookup_batch(batch_id, library)
        if rec is None:
            raise InputError(f"找不到批次：{batch_id}" + ("。请同时提供 library" if not library else ""))
        with self._lock:
            event = self._cancel_events.get(batch_id)
        if event is not None:
            event.set()
        store = _open_store(Path(rec["library"]))
        try:
            updated = store.request_cancel(batch_id)
        finally:
            store.close()
        persist_record(updated, write_store=False)
        with self._lock:
            self._batches[batch_id] = updated
        return _batch_status(updated)

    def list_batches(
        self,
        library: str,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if not library or not str(library).strip():
            raise InputError("请提供素材库目录 library")
        if limit is not None and limit < 1:
            raise InputError("limit 必须大于等于 1")
        root = Path(library).expanduser().resolve()
        records = _list_records(root, status=status, limit=limit)
        return {
            "library": str(root),
            "count": len(records),
            "records": [_batch_status(rec) for rec in records],
        }

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

    def read_item(self, library: str, id: str) -> dict[str, Any]:
        """读取已入库条目的元数据（含正文是否可读、正文长度，不含整篇正文）。"""
        return lookup_item(library, id)

    def read_transcript(
        self,
        library: str,
        id: str,
        *,
        kind: str = "auto",
        offset: int = 0,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """读取来源字幕或转写纯文本，按字符分页并返回总长度。"""
        kwargs: dict[str, Any] = {"kind": kind, "offset": offset}
        if limit is not None:
            kwargs["limit"] = limit
        return lookup_transcript(library, id, **kwargs)

    def search_library(
        self,
        library: str,
        query: str,
        *,
        limit: int = 20,
        in_text: bool = True,
    ) -> dict[str, Any]:
        """按标题、来源、ID 或正文检索素材库。"""
        return lookup_search(library, query, limit=limit, in_text=in_text)

    def get_metadata(
        self,
        target: str,
        library: str | None = None,
        *,
        cookies: str | None = None,
        cookies_from_browser: str | None = None,
        runner: Any | None = None,
    ) -> dict[str, Any]:
        """读取已入库条目，或在不下载的情况下探测链接 / 本地文件的标题、时长和字幕情况。"""
        options = None
        if cookies or cookies_from_browser:
            cookies_path = Path(cookies).expanduser() if cookies else None
            if cookies_path is not None and not cookies_path.is_file():
                raise InputError(f"找不到 cookies 文件：{cookies_path}")
            options = BatchOptions(
                library_root=Path(library) if library else Path("."),
                cookies_file=cookies_path,
                cookies_from_browser=cookies_from_browser,
            )
        return lookup_metadata(
            target,
            library=library,
            options=options,
            runner=runner if runner is not None else self._runner,
        )

    def _lookup_batch(self, batch_id: str, library: str | None) -> dict[str, Any] | None:
        loaded = load_record(Path(library), batch_id) if library else None
        with self._lock:
            if loaded is not None:
                self._batches[batch_id] = loaded
                return json.loads(json.dumps(loaded))
            rec = self._batches.get(batch_id)
            if rec is not None:
                return json.loads(json.dumps(rec))
        return None

    def _worker(self, batch_id: str, options: BatchOptions) -> None:
        self._update(batch_id, status="running")
        stop = threading.Event()
        watcher = threading.Thread(
            target=_watch_cancel,
            args=(batch_id, options.library_root, options.cancel_event, stop),
            name=f"media-intake-watch-{batch_id}",
            daemon=True,
        )
        watcher.start()
        try:
            with self._lock:
                items = list(self._batches[batch_id]["items"])
            if options.cancel_event is not None and options.cancel_event.is_set():
                self._update(batch_id, status="cancelled", error="已取消", exit_code=1)
                return
            kwargs: dict[str, Any] = {}
            if self._runner is not None:
                kwargs["runner"] = self._runner
            report = self._run_batch(items, options, **kwargs)
            self._update(batch_id, **_report_fields(report, cancelled=_was_cancelled(options, batch_id)))
        except Exception as exc:
            status = "cancelled" if _was_cancelled(options, batch_id) else "failed"
            self._update(batch_id, status=status, error=f"{type(exc).__name__}: {exc}", exit_code=1)
        finally:
            stop.set()

    def _update(self, batch_id: str, **fields: Any) -> None:
        with self._lock:
            rec = self._batches[batch_id]
            rec.update(fields)
            rec["updated_at"] = utc_now()
            snapshot = json.loads(json.dumps(rec))
        persist_record(snapshot)


def _require_transcriber(asr_backend: str) -> None:
    backend = (asr_backend or "faster-whisper").strip().lower().replace("_", "-")
    if backend in {"whisperx", "whisper-x"}:
        from media_intake.whisperx_backend import WHISPERX_INSTALL_HINT, whisperx_is_installed

        if not whisperx_is_installed():
            raise DependencyError(WHISPERX_INSTALL_HINT)
        return
    if not whisper_is_installed():
        raise DependencyError(WHISPER_INSTALL_HINT)


def _was_cancelled(options: BatchOptions, batch_id: str) -> bool:
    event = getattr(options, "cancel_event", None)
    if event is not None and event.is_set():
        return True
    store = _open_store(options.library_root)
    try:
        return store.is_cancel_requested(batch_id)
    finally:
        store.close()


def _watch_cancel(
    batch_id: str,
    library: Path,
    event: threading.Event | None,
    stop: threading.Event,
) -> None:
    if event is None:
        return
    while not stop.wait(0.2):
        store = _open_store(library)
        try:
            if store.is_cancel_requested(batch_id):
                event.set()
                return
        finally:
            store.close()


def _report_fields(report: RunReport, *, cancelled: bool) -> dict[str, Any]:
    jobs = [
        _job_brief(job)
        for job in (*report.succeeded, *report.skipped, *report.failed, *report.cancelled)
    ]
    counts = {
        "succeeded": len(report.succeeded),
        "skipped": len(report.skipped),
        "failed": len(report.failed),
        "cancelled": len(report.cancelled),
        "total": len(jobs),
    }
    if cancelled or report.cancelled:
        status = "cancelled"
    elif report.failed and (report.succeeded or report.skipped):
        status = "completed"
    elif report.failed:
        status = "failed"
    else:
        status = "succeeded"
    return {
        "status": status,
        "jobs": jobs,
        "counts": counts,
        "exit_code": report.exit_code,
        "error": "已取消" if status == "cancelled" and not report.failed else None,
    }


def _public_options(options: BatchOptions, *, transcribe: str) -> dict[str, Any]:
    return {
        "format": options.format,
        "audio_only": options.audio_only,
        "subs": options.write_subs,
        "sub_langs": options.sub_langs,
        "write_auto_subs": options.write_auto_subs,
        "transcribe": transcribe,
        "cookies": str(options.cookies_file) if options.cookies_file else None,
        "cookies_from_browser": options.cookies_from_browser,
        "extra_ytdlp_args": list(options.extra_ytdlp_args or []),
        "expand": options.expand,
        "max_items": options.max_items,
        "whisper_model": options.whisper_model,
        "whisper_device": options.whisper_device,
        "whisper_compute_type": options.whisper_compute_type,
        "whisper_language": options.whisper_language,
        "asr_backend": getattr(options, "asr_backend", "faster-whisper"),
        "diarize": bool(getattr(options, "diarize", False)),
        "concurrency": options.concurrency,
        "retries": options.retries,
        "retry_wait": options.retry_wait,
        "force": options.force,
        "reference_local": options.reference_local,
    }


def options_from_public(library: str | Path, data: dict[str, Any] | None) -> BatchOptions:
    data = data or {}
    cookies = data.get("cookies")
    return BatchOptions(
        library_root=Path(library),
        concurrency=int(data.get("concurrency") or 2),
        retries=int(data["retries"]) if data.get("retries") is not None else 3,
        retry_wait=float(data.get("retry_wait") if data.get("retry_wait") is not None else 1.0),
        format=data.get("format"),
        audio_only=bool(data.get("audio_only")),
        write_subs=True if data.get("subs") is None else bool(data.get("subs")),
        sub_langs=data.get("sub_langs"),
        write_auto_subs=bool(data.get("write_auto_subs")),
        cookies_file=Path(cookies) if cookies else None,
        cookies_from_browser=data.get("cookies_from_browser"),
        extra_ytdlp_args=list(data.get("extra_ytdlp_args") or []),
        force=bool(data.get("force")),
        reference_local=bool(data.get("reference_local")),
        expand=bool(data.get("expand")),
        max_items=data.get("max_items"),
        transcribe_mode=data.get("transcribe") or "never",
        whisper_model=data.get("whisper_model") or "base",
        whisper_device=data.get("whisper_device") or "auto",
        whisper_compute_type=data.get("whisper_compute_type") or "auto",
        whisper_language=data.get("whisper_language"),
        asr_backend=data.get("asr_backend") or "faster-whisper",
        diarize=bool(data.get("diarize")),
    )


def _job_brief(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "source": job.source,
        "status": job.status.value if hasattr(job.status, "value") else str(job.status),
        "title": job.title,
        "error": job.error,
    }


def _batch_status(rec: dict[str, Any]) -> dict[str, Any]:
    counts = rec.get("counts") or {}
    if "cancelled" not in counts:
        counts = dict(counts)
        counts.setdefault("cancelled", 0)
    return {
        "kind": "batch",
        "batch_id": rec["batch_id"],
        "status": rec["status"],
        "library": rec["library"],
        "item_count": rec.get("item_count") or len(rec.get("items") or []),
        "counts": counts,
        "jobs": rec.get("jobs") or [],
        "error": rec.get("error"),
        "exit_code": rec.get("exit_code"),
        "cancel_requested": bool(rec.get("cancel_requested")),
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


def _open_store(library: Path) -> BatchStore:
    root = Path(library).expanduser().resolve()
    Library(root).prepare()
    return BatchStore(root / STATE_DIR_NAME / "state.sqlite")


def persist_record(rec: dict[str, Any], *, write_store: bool = True) -> None:
    library = Path(rec["library"])
    path = _batch_path(library, rec["batch_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if write_store:
        store = _open_store(library)
        try:
            store.upsert(rec)
        finally:
            store.close()


def load_record(library: Path, batch_id: str) -> dict[str, Any] | None:
    root = Path(library).expanduser().resolve()
    store_path = root / STATE_DIR_NAME / "state.sqlite"
    if store_path.is_file():
        store = BatchStore(store_path)
        try:
            rec = store.get(batch_id)
        finally:
            store.close()
        if rec is not None:
            return rec
    path = _batch_path(root, batch_id)
    if not path.is_file():
        return None
    rec = json.loads(path.read_text(encoding="utf-8"))
    try:
        persist_record(rec)
    except OSError:
        pass
    return rec


def _list_records(library: Path, *, status: str | None, limit: int | None) -> list[dict[str, Any]]:
    root = Path(library).expanduser().resolve()
    store_path = root / STATE_DIR_NAME / "state.sqlite"
    records: list[dict[str, Any]] = []
    if store_path.is_file():
        store = BatchStore(store_path)
        try:
            records = store.list_all(status=status, limit=limit)
        finally:
            store.close()
        if records or not (root / STATE_DIR_NAME / "batches").is_dir():
            return records
    batch_dir = root / STATE_DIR_NAME / "batches"
    if not batch_dir.is_dir():
        return []
    loaded: list[dict[str, Any]] = []
    for path in sorted(batch_dir.glob("batch-*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if status and rec.get("status") != status:
            continue
        loaded.append(rec)
        if limit is not None and len(loaded) >= limit:
            break
    return loaded


# 兼容旧测试与调用仍使用的私有函数名。
_persist_batch = persist_record
_load_batch = load_record
