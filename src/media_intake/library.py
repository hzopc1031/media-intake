from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from media_intake.models import JobResult, JobStatus

MANIFEST_NAME = "manifest.jsonl"
INDEX_NAME = "index.md"
STATE_DIR_NAME = ".media-intake"
ITEMS_DIR_NAME = "items"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Library:
    """素材库目录布局（路径均相对库根目录）：

    media-library/
      manifest.jsonl          # 机器可读清单，每行一条，按 id 去重后重写
      index.md                # 人类可读索引
      .media-intake/state.sqlite
      items/<id>/
        media.<ext>           # 视频或音频
        source.srt            # 来源字幕（如有）
        source.txt            # 来源字幕纯文本（如有）
        transcript.srt        # 预留给后续离线转写
        transcript.txt
        *.info.json           # yt-dlp 元数据（下载模式）
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.items_dir = self.root / ITEMS_DIR_NAME
        self.state_path = self.root / STATE_DIR_NAME / "state.sqlite"
        self.manifest_path = self.root / MANIFEST_NAME
        self.index_path = self.root / INDEX_NAME

    def prepare(self) -> None:
        self.items_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def item_dir(self, job_id: str) -> Path:
        return self.items_dir / job_id

    def rel(self, path: Path | None) -> str | None:
        if path is None:
            return None
        resolved = path if path.is_absolute() else (self.root / path)
        try:
            return resolved.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return resolved.as_posix()

    def rewrite_outputs(self, jobs: list[JobResult], *, updated_at: str | None = None) -> None:
        stamp = updated_at or utc_now()
        self.prepare()
        lines = [json.dumps(manifest_record(job), ensure_ascii=False) + "\n" for job in jobs]
        self.manifest_path.write_text("".join(lines), encoding="utf-8")
        self.index_path.write_text(render_index(jobs, updated_at=stamp), encoding="utf-8")


def manifest_record(job: JobResult) -> dict:
    return {
        "id": job.id,
        "source": job.source,
        "source_kind": job.source_kind.value,
        "title": job.title,
        "duration_seconds": job.duration_seconds,
        "item_dir": job.item_dir,
        "media_path": job.media_path,
        "subtitle_path": job.subtitle_path,
        "subtitle_text_path": job.subtitle_text_path,
        "subtitle_kind": job.subtitle_kind.value if job.subtitle_kind else None,
        "transcript_path": job.transcript_path,
        "transcript_text_path": job.transcript_text_path,
        "parent_source": job.parent_source,
        "status": job.status.value,
        "error": job.error,
        "attempts": job.attempts,
    }


def render_index(jobs: list[JobResult], *, updated_at: str) -> str:
    lines = [
        "# 素材索引",
        "",
        f"更新时间（UTC）：{updated_at}",
        "",
        f"共 {len(jobs)} 条。",
        "",
        "| ID | 标题 | 来源 | 时长 | 媒体 | 字幕 | 转写 | 状态 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for job in jobs:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(job.id),
                    _cell(job.title or ""),
                    _cell(job.source),
                    _cell(_format_duration(job.duration_seconds)),
                    _cell(job.media_path or ""),
                    _cell(job.subtitle_path or ""),
                    _cell(job.transcript_path or ""),
                    _cell(_status_label(job.status)),
                ]
            )
            + " |"
        )
    failed = [j for j in jobs if j.status == JobStatus.FAILED]
    if failed:
        lines.extend(["", "## 失败条目", ""])
        for job in failed:
            lines.append(f"- `{job.id}` {job.source}：{job.error or '未知错误'}")
    lines.append("")
    return "\n".join(lines)


def _status_label(status: JobStatus) -> str:
    return {
        JobStatus.SUCCEEDED: "成功",
        JobStatus.FAILED: "失败",
        JobStatus.SKIPPED: "跳过",
        JobStatus.PENDING: "待处理",
        JobStatus.RUNNING: "进行中",
    }.get(status, status.value)


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
