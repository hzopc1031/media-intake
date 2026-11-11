from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SourceKind(str, Enum):
    URL = "url"
    LOCAL = "local"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class SubtitleKind(str, Enum):
    SOURCE = "source"
    AUTO = "auto"


@dataclass(frozen=True)
class Source:
    raw: str
    kind: SourceKind
    identity: str  # stable id material (normalized URL or resolved path)
    parent_source: str | None = None


@dataclass
class JobResult:
    id: str
    source: str
    source_kind: SourceKind
    status: JobStatus
    title: str | None = None
    duration_seconds: float | None = None
    item_dir: str | None = None
    media_path: str | None = None
    subtitle_path: str | None = None
    subtitle_text_path: str | None = None
    subtitle_kind: SubtitleKind | None = None
    transcript_path: str | None = None
    transcript_text_path: str | None = None
    transcript_model: str | None = None
    transcript_language: str | None = None
    parent_source: str | None = None
    error: str | None = None
    attempts: int = 0


@dataclass
class RunReport:
    succeeded: list[JobResult] = field(default_factory=list)
    skipped: list[JobResult] = field(default_factory=list)
    failed: list[JobResult] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0
