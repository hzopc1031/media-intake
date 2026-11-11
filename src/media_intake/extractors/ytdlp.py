from __future__ import annotations

import json
from pathlib import Path

from media_intake.config import BatchOptions
from media_intake.download import download_url
from media_intake.errors import JobError
from media_intake.extractors.base import ExtractedItem
from media_intake.process import CommandRunner
from media_intake.redact import redact_argv

EXPAND_TIMEOUT = 60 * 10

# 当扁平条目只有 id、没有完整 URL 时，按提取器拼出常用页面地址。
_ID_URL_TEMPLATES = {
    "youtube": "https://www.youtube.com/watch?v={id}",
    "youtubeyt": "https://www.youtube.com/watch?v={id}",
    "youtubetab": "https://www.youtube.com/watch?v={id}",
    "vimeo": "https://vimeo.com/{id}",
    "bilibili": "https://www.bilibili.com/video/{id}",
}


class YtDlpBackend:
    """默认后端：用本机 yt-dlp 列出条目并下载。始终 matches，应放在注册表末尾。"""

    name = "yt-dlp"

    def __init__(self, ytdlp: Path) -> None:
        self.ytdlp = Path(ytdlp)

    def matches(self, url: str) -> bool:
        return url.startswith(("http://", "https://"))

    def expand(
        self,
        url: str,
        options: BatchOptions,
        runner: CommandRunner,
        *,
        max_items: int | None,
    ) -> list[ExtractedItem]:
        argv = build_expand_argv(self.ytdlp, url, options, max_items)
        result = runner.run(argv, timeout=EXPAND_TIMEOUT)
        if result.returncode != 0:
            reason = _short_error(result.stderr or result.stdout)
            raise JobError(
                "展开播放列表或主页失败（命令："
                + " ".join(redact_argv(argv[:-1]) + ["<url>"])
                + f"）：{reason}"
            )
        items = parse_flat_entries(result.stdout, max_items=max_items)
        if not items:
            raise JobError("展开播放列表或主页后没有得到可用的条目")
        return items

    def download(
        self,
        url: str,
        dest_dir: Path,
        options: BatchOptions,
        runner: CommandRunner,
    ) -> dict:
        return download_url(url, dest_dir, options, self.ytdlp, runner)


def build_expand_argv(
    ytdlp: Path,
    url: str,
    options: BatchOptions,
    max_items: int | None,
) -> list[str]:
    argv: list[str] = [
        str(ytdlp),
        "--ignore-config",
        "--flat-playlist",
        "--skip-download",
        "--no-warnings",
        "--print",
        "%({id,url,webpage_url,title,duration,ie_key,extractor}j)",
    ]
    if max_items is not None:
        argv.extend(["--playlist-end", str(max_items)])
    if options.cookies_file is not None:
        argv.extend(["--cookies", str(options.cookies_file)])
    if options.cookies_from_browser:
        argv.extend(["--cookies-from-browser", options.cookies_from_browser])
    argv.extend(options.extra_ytdlp_args)
    argv.append(url)
    return argv


def parse_flat_entries(stdout: str, *, max_items: int | None = None) -> list[ExtractedItem]:
    items: list[ExtractedItem] = []
    seen: set[str] = set()
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        url = resolve_entry_url(entry)
        if not url or url in seen:
            continue
        seen.add(url)
        duration = entry.get("duration")
        try:
            duration_seconds = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_seconds = None
        title = entry.get("title")
        items.append(
            ExtractedItem(
                url=url,
                title=str(title).strip() if isinstance(title, str) and title.strip() else None,
                duration_seconds=duration_seconds,
            )
        )
        if max_items is not None and len(items) >= max_items:
            break
    return items


def resolve_entry_url(entry: dict) -> str | None:
    for key in ("webpage_url", "original_url", "url"):
        value = entry.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    entry_id = entry.get("id")
    if not isinstance(entry_id, str) or not entry_id:
        return None
    ie = str(entry.get("ie_key") or entry.get("extractor") or "").lower().replace(" ", "")
    template = _ID_URL_TEMPLATES.get(ie)
    if template:
        return template.format(id=entry_id)
    return None


def _short_error(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "无错误输出"
    for line in reversed(lines):
        if "ERROR" in line.upper():
            return line[:500]
    return lines[-1][:500]
