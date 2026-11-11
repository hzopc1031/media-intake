from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from media_intake.models import JobResult, JobStatus, SourceKind, SubtitleKind

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    title TEXT,
    duration_seconds REAL,
    item_dir TEXT,
    media_path TEXT,
    subtitle_path TEXT,
    subtitle_text_path TEXT,
    subtitle_kind TEXT,
    transcript_path TEXT,
    transcript_text_path TEXT,
    transcript_model TEXT,
    transcript_language TEXT,
    transcript_engine TEXT,
    transcript_words_path TEXT,
    transcript_diarized INTEGER,
    parent_source TEXT,
    platform TEXT,
    uploader TEXT,
    upload_date TEXT,
    thumbnail_path TEXT,
    extra_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
"""


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            cols = {row[1] for row in self._conn.execute("PRAGMA table_info(jobs)")}
            for column, decl in (
                ("parent_source", "TEXT"),
                ("transcript_model", "TEXT"),
                ("transcript_language", "TEXT"),
                ("transcript_engine", "TEXT"),
                ("transcript_words_path", "TEXT"),
                ("transcript_diarized", "INTEGER"),
                ("platform", "TEXT"),
                ("uploader", "TEXT"),
                ("upload_date", "TEXT"),
                ("thumbnail_path", "TEXT"),
            ):
                if column not in cols:
                    self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {decl}")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get(self, job_id: str) -> JobResult | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_result(row) if row else None

    def list_all(self) -> list[JobResult]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY created_at, id").fetchall()
        return [_row_to_result(r) for r in rows]

    def upsert(
        self,
        result: JobResult,
        *,
        created_at: str,
        updated_at: str,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        existing = self.get(result.id)
        created = created_at if existing is None else (_get_times(self, result.id)[0] or created_at)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (
                    id, source, source_kind, status, attempts, error, title,
                    duration_seconds, item_dir, media_path, subtitle_path,
                    subtitle_text_path, subtitle_kind, transcript_path,
                    transcript_text_path, transcript_model, transcript_language,
                    transcript_engine, transcript_words_path, transcript_diarized,
                    parent_source, platform, uploader, upload_date, thumbnail_path,
                    extra_json, created_at, updated_at,
                    started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source=excluded.source,
                    source_kind=excluded.source_kind,
                    status=excluded.status,
                    attempts=excluded.attempts,
                    error=excluded.error,
                    title=excluded.title,
                    duration_seconds=excluded.duration_seconds,
                    item_dir=excluded.item_dir,
                    media_path=excluded.media_path,
                    subtitle_path=excluded.subtitle_path,
                    subtitle_text_path=excluded.subtitle_text_path,
                    subtitle_kind=excluded.subtitle_kind,
                    transcript_path=excluded.transcript_path,
                    transcript_text_path=excluded.transcript_text_path,
                    transcript_model=excluded.transcript_model,
                    transcript_language=excluded.transcript_language,
                    transcript_engine=excluded.transcript_engine,
                    transcript_words_path=excluded.transcript_words_path,
                    transcript_diarized=excluded.transcript_diarized,
                    parent_source=excluded.parent_source,
                    platform=excluded.platform,
                    uploader=excluded.uploader,
                    upload_date=excluded.upload_date,
                    thumbnail_path=excluded.thumbnail_path,
                    extra_json=excluded.extra_json,
                    updated_at=excluded.updated_at,
                    started_at=COALESCE(excluded.started_at, jobs.started_at),
                    finished_at=COALESCE(excluded.finished_at, jobs.finished_at)
                """,
                (
                    result.id,
                    result.source,
                    result.source_kind.value,
                    result.status.value,
                    result.attempts,
                    result.error,
                    result.title,
                    result.duration_seconds,
                    result.item_dir,
                    result.media_path,
                    result.subtitle_path,
                    result.subtitle_text_path,
                    result.subtitle_kind.value if result.subtitle_kind else None,
                    result.transcript_path,
                    result.transcript_text_path,
                    result.transcript_model,
                    result.transcript_language,
                    result.transcript_engine,
                    result.transcript_words_path,
                    None if result.transcript_diarized is None else (1 if result.transcript_diarized else 0),
                    result.parent_source,
                    result.platform,
                    result.uploader,
                    result.upload_date,
                    result.thumbnail_path,
                    None,
                    created,
                    updated_at,
                    started_at,
                    finished_at,
                ),
            )
            self._conn.commit()


def _get_times(store: JobStore, job_id: str) -> tuple[str | None, str | None]:
    with store._lock:
        row = store._conn.execute(
            "SELECT created_at, updated_at FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if not row:
        return None, None
    return row["created_at"], row["updated_at"]


def _row_to_result(row: sqlite3.Row) -> JobResult:
    kind = SubtitleKind(row["subtitle_kind"]) if row["subtitle_kind"] else None
    keys = row.keys()
    return JobResult(
        id=row["id"],
        source=row["source"],
        source_kind=SourceKind(row["source_kind"]),
        status=JobStatus(row["status"]),
        title=row["title"],
        duration_seconds=row["duration_seconds"],
        item_dir=row["item_dir"],
        media_path=row["media_path"],
        subtitle_path=row["subtitle_path"],
        subtitle_text_path=row["subtitle_text_path"],
        subtitle_kind=kind,
        transcript_path=row["transcript_path"],
        transcript_text_path=row["transcript_text_path"],
        transcript_model=row["transcript_model"] if "transcript_model" in keys else None,
        transcript_language=row["transcript_language"] if "transcript_language" in keys else None,
        transcript_engine=row["transcript_engine"] if "transcript_engine" in keys else None,
        transcript_words_path=row["transcript_words_path"] if "transcript_words_path" in keys else None,
        transcript_diarized=_bool_or_none(row["transcript_diarized"]) if "transcript_diarized" in keys else None,
        parent_source=row["parent_source"] if "parent_source" in keys else None,
        platform=row["platform"] if "platform" in keys else None,
        uploader=row["uploader"] if "uploader" in keys else None,
        upload_date=row["upload_date"] if "upload_date" in keys else None,
        thumbnail_path=row["thumbnail_path"] if "thumbnail_path" in keys else None,
        error=row["error"],
        attempts=row["attempts"],
    )


def _bool_or_none(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)
