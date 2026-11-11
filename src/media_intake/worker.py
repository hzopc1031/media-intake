"""脱离 MCP 客户端独立运行的批次 worker。"""

from __future__ import annotations

import os
import socket
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from media_intake.batch_store import BatchStore
from media_intake.batches import _report_fields, options_from_public, persist_record
from media_intake.library import STATE_DIR_NAME, Library
from media_intake.models import RunReport
from media_intake.pipeline import run_batch

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_STALE_AFTER = 120.0
DEFAULT_HEARTBEAT_INTERVAL = 5.0

RunBatchFn = Callable[..., RunReport]


def new_worker_id() -> str:
    host = socket.gethostname().split(".")[0] or "worker"
    return f"{host}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def run_worker(
    library: str | Path,
    *,
    once: bool = False,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    stale_after: float = DEFAULT_STALE_AFTER,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
    run_batch_fn: RunBatchFn | None = None,
    runner: Any | None = None,
    stop_event: threading.Event | None = None,
    worker_id: str | None = None,
) -> int:
    """领取并处理素材库中的排队批次。once=True 时最多处理一条后退出。"""
    root = Path(library).expanduser().resolve()
    Library(root).prepare()
    store = BatchStore(root / STATE_DIR_NAME / "state.sqlite")
    identity = worker_id or new_worker_id()
    run_fn = run_batch_fn or run_batch
    processed = 0
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            rec = store.claim_next(identity, stale_after=stale_after)
            if rec is None:
                if once:
                    break
                if stop_event is not None and stop_event.wait(poll_interval):
                    break
                elif stop_event is None:
                    threading.Event().wait(poll_interval)
                continue
            _run_claimed(
                rec,
                store=store,
                worker_id=identity,
                run_fn=run_fn,
                runner=runner,
                heartbeat_interval=heartbeat_interval,
            )
            processed += 1
            if once:
                break
    finally:
        store.close()
    return 0 if processed or once else 0


def _run_claimed(
    rec: dict[str, Any],
    *,
    store: BatchStore,
    worker_id: str,
    run_fn: RunBatchFn,
    runner: Any | None,
    heartbeat_interval: float,
) -> None:
    batch_id = rec["batch_id"]
    cancel_event = threading.Event()
    stop = threading.Event()
    if rec.get("cancel_requested"):
        rec["status"] = "cancelled"
        rec["error"] = rec.get("error") or "已取消"
        rec["exit_code"] = 1
        store.release(batch_id, worker_id, rec)
        persist_record(rec, write_store=False)
        return

    def _beat() -> None:
        store.heartbeat(batch_id, worker_id)
        while not stop.wait(heartbeat_interval):
            store.heartbeat(batch_id, worker_id)
            if store.is_cancel_requested(batch_id):
                cancel_event.set()
                return

    thread = threading.Thread(target=_beat, name=f"heartbeat-{batch_id}", daemon=True)
    thread.start()
    rec["status"] = "running"
    rec["claimed_by"] = worker_id
    persist_record(rec, write_store=False)
    try:
        options = options_from_public(rec["library"], rec.get("options") or {})
        options.cancel_event = cancel_event
        if store.is_cancel_requested(batch_id):
            cancel_event.set()
            rec["status"] = "cancelled"
            rec["error"] = "已取消"
            rec["exit_code"] = 1
            return
        kwargs: dict[str, Any] = {}
        if runner is not None:
            kwargs["runner"] = runner
        report = run_fn(list(rec.get("items") or []), options, **kwargs)
        rec.update(_report_fields(report, cancelled=cancel_event.is_set() or store.is_cancel_requested(batch_id)))
    except Exception as exc:
        cancelled = cancel_event.is_set() or store.is_cancel_requested(batch_id)
        rec["status"] = "cancelled" if cancelled else "failed"
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["exit_code"] = 1
    finally:
        stop.set()
        rec["claimed_by"] = None
        rec["claimed_at"] = None
        rec["heartbeat_at"] = None
        store.release(batch_id, worker_id, rec)
        persist_record(rec, write_store=False)
