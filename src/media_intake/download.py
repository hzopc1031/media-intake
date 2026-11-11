from __future__ import annotations

import json
from pathlib import Path

from media_intake.config import BatchOptions
from media_intake.errors import JobError
from media_intake.process import CommandRunner
from media_intake.redact import format_redacted_command, redact_error_text

# 下载超时按单条任务计，足够长以覆盖普通视频；测试可注入更小值。
DEFAULT_TIMEOUT = 60 * 30



def subtitle_langs_for_ytdlp(sub_langs: str | None) -> tuple[str, bool]:
    """Return (--sub-langs value, whether to pass --convert-subs srt).

    yt-dlp exposes Bilibili danmaku (comment XML) as a subtitle language
    named "danmaku". Converting that XML with --convert-subs srt fails
    ("Invalid data found when processing input") and aborts the whole
    download even when the media file itself was written. Danmaku is
    excluded by default. If the caller explicitly requests danmaku,
    conversion is skipped so the XML can be saved without failing the job.
    """
    tokens = [part.strip() for part in (sub_langs or "all").split(",") if part.strip()]
    if not tokens:
        tokens = ["all"]
    lowered = [token.lower() for token in tokens]
    wants_danmaku = "danmaku" in lowered
    if not wants_danmaku and "-danmaku" not in lowered:
        tokens.append("-danmaku")
    return ",".join(tokens), not wants_danmaku


def build_ytdlp_argv(
    ytdlp: Path,
    url: str,
    dest_dir: Path,
    options: BatchOptions,
) -> list[str]:
    argv: list[str] = [
        str(ytdlp),
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--newline",
        "--no-mtime",
        "--continue",
        "--write-info-json",
        "--merge-output-format",
        "mp4",
        "-o",
        str(dest_dir / "media.%(ext)s"),
    ]
    if options.format:
        argv.extend(["-f", options.format])
    if options.audio_only:
        argv.extend(["-x", "--audio-format", "m4a", "--audio-quality", "0"])
    if options.write_subs:
        argv.extend(["--write-subs"])
        langs, convert = subtitle_langs_for_ytdlp(options.sub_langs)
        if convert:
            argv.extend(["--convert-subs", "srt"])
        argv.extend(["--sub-langs", langs])
        if options.write_auto_subs:
            argv.append("--write-auto-subs")
    if options.cookies_file is not None:
        argv.extend(["--cookies", str(options.cookies_file)])
    if options.cookies_from_browser:
        argv.extend(["--cookies-from-browser", options.cookies_from_browser])
    argv.extend(options.extra_ytdlp_args)
    argv.append(url)
    return argv


def download_url(
    url: str,
    dest_dir: Path,
    options: BatchOptions,
    ytdlp: Path,
    runner: CommandRunner,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if options.cookies_file is not None and not options.cookies_file.is_file():
        raise JobError(f"找不到 cookies 文件：{options.cookies_file}")
    argv = build_ytdlp_argv(ytdlp, url, dest_dir, options)
    result = runner.run(argv, cwd=dest_dir, timeout=timeout)
    if result.returncode != 0:
        reason = _short_error(result.stderr or result.stdout)
        raise JobError(
            "yt-dlp 下载失败（命令："
            + format_redacted_command(argv)
            + f"）：{reason}"
        )
    info = _read_info_json(dest_dir)
    return info


def _read_info_json(dest_dir: Path) -> dict:
    files = sorted(dest_dir.glob("*.info.json"))
    if not files:
        return {}
    # 取最新写入的一份
    path = max(files, key=lambda p: p.stat().st_mtime)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _short_error(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "无错误输出"
    # 优先 ERROR 行，避免把 cookie 文件内容一类日志整段带出
    for line in reversed(lines):
        upper = line.upper()
        if "ERROR" in upper or "ERROR:" in upper:
            return redact_error_text(line[:500])
    return redact_error_text(lines[-1][:500])


def pick_media_file(dest_dir: Path) -> Path | None:
    skip_suffixes = {
        ".srt",
        ".vtt",
        ".ass",
        ".json",
        ".txt",
        ".part",
        ".ytdl",
        ".description",
    }
    skip_names = {"source.srt", "source.txt", "transcript.srt", "transcript.txt"}
    candidates: list[Path] = []
    for path in dest_dir.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.name in skip_names:
            continue
        if path.suffix.lower() in skip_suffixes:
            continue
        if path.name.endswith(".info.json"):
            continue
        candidates.append(path)
    if not candidates:
        return None
    # 优先常见媒体扩展名
    media_ext = {
        ".mp4",
        ".mkv",
        ".webm",
        ".m4a",
        ".mp3",
        ".opus",
        ".wav",
        ".mov",
        ".ogg",
        ".flac",
        ".aac",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
    }
    preferred = [p for p in candidates if p.suffix.lower() in media_ext]
    pool = preferred or candidates
    named = [p for p in pool if p.stem == "media" or p.stem.startswith("media-")]
    chosen = named or pool
    # 优先固定的 media.*，否则取最大文件
    exact = [p for p in chosen if p.stem == "media"]
    if exact:
        return exact[0]
    return max(chosen, key=lambda p: p.stat().st_size)


def pick_subtitle_files(dest_dir: Path) -> list[Path]:
    files = [
        p
        for p in dest_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".srt", ".vtt"} and p.name not in {"source.srt", "transcript.srt"}
    ]
    return sorted(files, key=lambda p: p.name.lower())
