"""批次队列的 SQLite 存储：入队、领取、心跳、取消和崩溃回收。"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from media_intake.errors import InputError
from media_intake.library import utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    library TEXT NOT NULL,
    items_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    counts_json TEXT,
    jobs_json TEXT,
    error TEXT,
    exit_code INTEGER,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    claimed_by TEXT,
    claimed_at TEXT,
    heartbeat_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batches_status_created ON batches(status, created_at);
"""

_TERMINAL = frozenset({"succeeded", "completed", "failed", "cancelled"})


def _cutoff(stale_after: float) -> str:
    when = datetime.now(timezone.utc) - timedelta(seconds=max(0.0, stale_after))
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class BatchStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def upsert(self, rec: dict[str, Any]) -> None:
        payload = _normalize(rec)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO batches (
                    batch_id, status, library, items_json, options_json,
                    counts_json, jobs_json, error, exit_code, cancel_requested,
                    claimed_by, claimed_at, heartbeat_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                    status=excluded.status,
                    library=excluded.library,
                    items_json=excluded.items_json,
                    options_json=excluded.options_json,
                    counts_json=excluded.counts_json,
                    jobs_json=excluded.jobs_json,
                    error=excluded.error,
                    exit_code=excluded.exit_code,
                    cancel_requested=excluded.cancel_requested,
                    claimed_by=excluded.claimed_by,
                    claimed_at=excluded.claimed_at,
                    heartbeat_at=excluded.heartbeat_at,
                    updated_at=excluded.updated_at
                """,
                (
                    payload["batch_id"],
                    payload["status"],
                    payload["library"],
                    json.dumps(payload["items"], ensure_ascii=False),
                    json.dumps(payload.get("options") or {}, ensure_ascii=False),
                    json.dumps(payload.get("counts") or {}, ensure_ascii=False),
                    json.dumps(payload.get("jobs") or [], ensure_ascii=False),
                    payload.get("error"),
                    payload.get("exit_code"),
                    1 if payload.get("cancel_requested") else 0,
                    payload.get("claimed_by"),
                    payload.get("claimed_at"),
                    payload.get("heartbeat_at"),
                    payload["created_at"],
                    payload["updated_at"],
                ),
            )
            self._conn.commit()

    def get(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        return _row_to_record(row) if row else None

    def list_all(self, *, status: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM batches"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at, batch_id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def request_cancel(self, batch_id: str) -> dict[str, Any]:
        rec = self.get(batch_id)
        if rec is None:
            raise InputError(f"找不到批次：{batch_id}")
        if rec["status"] in _TERMINAL:
            return rec
        rec["cancel_requested"] = True
        rec["updated_at"] = utc_now()
        if rec["status"] == "queued":
            rec["status"] = "cancelled"
            rec["error"] = rec.get("error") or "已取消"
            rec["exit_code"] = 1
            rec["claimed_by"] = None
            rec["claimed_at"] = None
            rec["heartbeat_at"] = None
        self.upsert(rec)
        return rec

    def is_cancel_requested(self, batch_id: str) -> bool:
        rec = self.get(batch_id)
        return bool(rec and rec.get("cancel_requested"))

    def heartbeat(self, batch_id: str, worker_id: str) -> bool:
        now = utc_now()
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE batches
                SET heartbeat_at = ?, updated_at = ?
                WHERE batch_id = ? AND claimed_by = ? AND status = 'running'
                """,
                (now, now, batch_id, worker_id),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def claim_next(self, worker_id: str, *, stale_after: float = 120.0) -> dict[str, Any] | None:
        """领取下一条排队中或心跳过期的运行中批次。"""
        cutoff = _cutoff(stale_after)
        now = utc_now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    """
                    SELECT * FROM batches
                    WHERE cancel_requested = 0
                      AND (
                        status = 'queued'
                        OR (
                            status = 'running'
                            AND (heartbeat_at IS NULL OR heartbeat_at < ?)
                        )
                      )
                    ORDER BY created_at, batch_id
                    LIMIT 1
                    """,
                    (cutoff,),
                ).fetchone()
                if row is None:
                    # 已请求取消但仍卡在 queued 的，收尾为 cancelled
                    stale_cancel = self._conn.execute(
                        """
                        SELECT * FROM batches
                        WHERE cancel_requested = 1 AND status = 'queued'
                        ORDER BY created_at, batch_id
                        LIMIT 1
                        """
                    ).fetchone()
                    if stale_cancel is not None:
                        self._conn.execute(
                            """
                            UPDATE batches
                            SET status = 'cancelled', error = COALESCE(error, '已取消'),
                                exit_code = 1, claimed_by = NULL, claimed_at = NULL,
                                heartbeat_at = NULL, updated_at = ?
                            WHERE batch_id = ?
                            """,
                            (now, stale_cancel["batch_id"]),
                        )
                    self._conn.commit()
                    return None
                batch_id = row["batch_id"]
                self._conn.execute(
                    """
                    UPDATE batches
                    SET status = 'running', claimed_by = ?, claimed_at = ?,
                        heartbeat_at = ?, updated_at = ?
                    WHERE batch_id = ?
                    """,
                    (worker_id, now, now, now, batch_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        rec = self.get(batch_id)
        return rec

    def release(self, batch_id: str, worker_id: str, rec: dict[str, Any]) -> None:
        payload = _normalize(rec)
        if payload["status"] in _TERMINAL:
            payload["claimed_by"] = None
            payload["claimed_at"] = None
            payload["heartbeat_at"] = None
        payload["updated_at"] = utc_now()
        # 仅当仍由本 worker 持有，或记录尚未被他人领走时写回
        current = self.get(batch_id)
        if current is not None and current.get("claimed_by") not in {None, worker_id}:
            return
        self.upsert(payload)


def _normalize(rec: dict[str, Any]) -> dict[str, Any]:
    data = dict(rec)
    data.setdefault("items", [])
    data.setdefault("options", {})
    data.setdefault("counts", {})
    data.setdefault("jobs", [])
    data.setdefault("cancel_requested", False)
    data.setdefault("created_at", utc_now())
    data.setdefault("updated_at", data["created_at"])
    data["cancel_requested"] = bool(data.get("cancel_requested"))
    return data


def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
    def _load(text: str | None, default: Any) -> Any:
        if not text:
            return default
        return json.loads(text)

    rec = {
        "batch_id": row["batch_id"],
        "status": row["status"],
        "library": row["library"],
        "items": _load(row["items_json"], []),
        "item_count": 0,
        "options": _load(row["options_json"], {}),
        "counts": _load(row["counts_json"], {}),
        "jobs": _load(row["jobs_json"], []),
        "error": row["error"],
        "exit_code": row["exit_code"],
        "cancel_requested": bool(row["cancel_requested"]),
        "claimed_by": row["claimed_by"],
        "claimed_at": row["claimed_at"],
        "heartbeat_at": row["heartbeat_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    rec["item_count"] = rec.get("item_count") or len(rec["items"])
    if "item_count" not in _load(row["items_json"], []) and row["items_json"]:
        rec["item_count"] = len(rec["items"])
    return rec
