from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from media_intake.errors import JobError
from media_intake.process import CommandRunner


def ffprobe_media(ffprobe: Path, media_path: Path, runner: CommandRunner) -> dict:
    argv = [
        str(ffprobe),
        "-v",
        "error",
        "-show_entries",
        "format=duration:format_tags=title,TITLE",
        "-of",
        "json",
        str(media_path),
    ]
    result = runner.run(argv, timeout=60)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:500] or "ffprobe 失败"
        raise JobError(f"无法探测本地文件 {media_path.name}：{detail}")
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise JobError(f"ffprobe 输出不是有效 JSON：{exc}") from exc
    fmt = data.get("format") or {}
    tags = fmt.get("tags") or {}
    title = tags.get("title") or tags.get("TITLE")
    duration = fmt.get("duration")
    duration_seconds: float | None
    try:
        duration_seconds = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None
    return {
        "title": str(title).strip() if title else None,
        "duration_seconds": duration_seconds,
    }


def archive_local_file(
    source: Path,
    dest_dir: Path,
    *,
    reference: bool,
) -> Path:
    if not source.is_file():
        raise JobError(f"本地文件不存在：{source}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"media{source.suffix.lower() or source.suffix}"
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    try:
        if source.resolve() == dest.resolve():
            return dest
    except OSError:
        pass
    if reference:
        try:
            os.link(source, dest)
            return dest
        except OSError:
            try:
                os.symlink(source, dest)
                return dest
            except OSError:
                pass
        shutil.copy2(source, dest)
        return dest
    shutil.copy2(source, dest)
    return dest


def copy_sidecar_subtitle(src: Path, dest_dir: Path, dest_name: str) -> Path:
    dest = dest_dir / dest_name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return dest
