"""从清单、本地文件或 yt-dlp 探测元数据（不下载媒体）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from media_intake.config import BatchOptions
from media_intake.deps import require_ffmpeg, require_ytdlp
from media_intake.errors import InputError, JobError
from media_intake.inputs import classify
from media_intake.models import SourceKind
from media_intake.probe import ffprobe_media
from media_intake.process import CommandRunner, SubprocessRunner
from media_intake.query import read_item
from media_intake.subtitles import find_sidecar_subtitles

PROBE_TIMEOUT = 60 * 2


def fields_from_info(info: dict[str, Any] | None) -> dict[str, Any]:
    """从 yt-dlp 的 info.json 取出清单要用的补充字段。"""
    info = info or {}
    platform = info.get("extractor_key") or info.get("extractor") or info.get("ie_key")
    if isinstance(platform, str):
        platform = platform.strip() or None
    else:
        platform = None
    uploader = (
        info.get("uploader")
        or info.get("channel")
        or info.get("creator")
        or info.get("artist")
        or info.get("uploader_id")
    )
    if isinstance(uploader, str):
        uploader = uploader.strip() or None
    else:
        uploader = None
    upload_date = info.get("upload_date") or info.get("release_date")
    if isinstance(upload_date, str):
        upload_date = upload_date.strip() or None
    else:
        upload_date = None
    return {
        "platform": platform,
        "uploader": uploader,
        "upload_date": upload_date,
    }


def subtitle_flags(info: dict[str, Any] | None) -> tuple[bool, bool]:
    info = info or {}
    has_subs = bool(info.get("subtitles"))
    has_auto = bool(info.get("automatic_captions"))
    return has_subs, has_auto


def get_metadata(
    target: str,
    *,
    library: str | None = None,
    options: BatchOptions | None = None,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """读取已入库条目的元数据，或在不下载的情况下探测链接 / 本地文件。"""
    text = (target or "").strip()
    if not text:
        raise InputError("请提供条目 ID、链接或本地文件路径")
    if library and _looks_like_item_id(text):
        try:
            rec = read_item(library, text)
            rec["kind"] = "item"
            return rec
        except InputError:
            # 12 位十六进制也可能碰巧是文件名；再按来源探测。
            pass
    return probe_source(text, options=options, runner=runner)


def probe_source(
    target: str,
    *,
    options: BatchOptions | None = None,
    runner: CommandRunner | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    runner = runner or SubprocessRunner()
    source = classify(target, cwd=cwd)
    if source.kind is SourceKind.LOCAL:
        return _probe_local(Path(source.identity), runner)
    ytdlp = require_ytdlp(runner)
    opts = options or BatchOptions(library_root=Path(".").resolve())
    return _probe_url(source.raw, ytdlp, opts, runner)


def _looks_like_item_id(value: str) -> bool:
    return len(value) == 12 and all(c in "0123456789abcdefABCDEF" for c in value)


def _probe_local(path: Path, runner: CommandRunner) -> dict[str, Any]:
    if not path.is_file():
        raise InputError(f"本地文件不存在：{path}")
    _, ffprobe = require_ffmpeg(runner)
    meta = ffprobe_media(ffprobe, path, runner)
    sidecars = find_sidecar_subtitles(path)
    return {
        "kind": "probe",
        "source": str(path),
        "source_kind": "local",
        "title": meta.get("title") or path.stem,
        "duration_seconds": meta.get("duration_seconds"),
        "has_subtitles": bool(sidecars),
        "has_automatic_captions": False,
        "platform": None,
        "uploader": None,
        "upload_date": None,
    }


def _probe_url(
    url: str,
    ytdlp: Path,
    options: BatchOptions,
    runner: CommandRunner,
) -> dict[str, Any]:
    argv = [
        str(ytdlp),
        "--ignore-config",
        "--no-playlist",
        "--skip-download",
        "--no-warnings",
        "--dump-json",
    ]
    if options.cookies_file is not None:
        if not options.cookies_file.is_file():
            raise InputError(f"找不到 cookies 文件：{options.cookies_file}")
        argv.extend(["--cookies", str(options.cookies_file)])
    if options.cookies_from_browser:
        argv.extend(["--cookies-from-browser", options.cookies_from_browser])
    argv.extend(options.extra_ytdlp_args)
    argv.append(url)
    result = runner.run(argv, timeout=PROBE_TIMEOUT)
    if result.returncode != 0 or not (result.stdout or "").strip():
        detail = (result.stderr or result.stdout or "yt-dlp 探测失败").strip().splitlines()
        short = detail[-1] if detail else "yt-dlp 探测失败"
        raise JobError(f"探测元数据失败：{short}")
    try:
        info = json.loads(result.stdout.strip().splitlines()[0])
    except json.JSONDecodeError as exc:
        raise JobError(f"yt-dlp 探测输出不是有效 JSON：{exc}") from exc
    if not isinstance(info, dict):
        raise JobError("yt-dlp 探测输出缺少条目对象")
    extra = fields_from_info(info)
    has_subs, has_auto = subtitle_flags(info)
    duration = info.get("duration")
    try:
        duration_seconds = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None
    title = info.get("title") or info.get("fulltitle")
    return {
        "kind": "probe",
        "source": url,
        "source_kind": "url",
        "title": str(title).strip() if isinstance(title, str) and title.strip() else None,
        "duration_seconds": duration_seconds,
        "has_subtitles": has_subs,
        "has_automatic_captions": has_auto,
        "is_live": bool(info.get("is_live")),
        **extra,
    }
