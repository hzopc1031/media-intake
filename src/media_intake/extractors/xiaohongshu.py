"""小红书专用提取器占位，本版本尚未实现，也未注册到默认后端列表。

实现 MediaBackend 后，把它插到 default_registry() 里 YtDlpBackend 之前即可。
在此之前，小红书链接仍由默认的 yt-dlp 后端处理。
"""

from __future__ import annotations

from pathlib import Path

from media_intake.config import BatchOptions
from media_intake.extractors.base import ExtractedItem, host_matches, url_host
from media_intake.process import CommandRunner

XIAOHONGSHU_HOSTS = (
    "xiaohongshu.com",
    "xhslink.com",
)


def matches(url: str) -> bool:
    return host_matches(url_host(url), XIAOHONGSHU_HOSTS)


class XiaohongshuBackend:
    name = "xiaohongshu"

    def matches(self, url: str) -> bool:
        return matches(url)

    def expand(
        self,
        url: str,
        options: BatchOptions,
        runner: CommandRunner,
        *,
        max_items: int | None,
    ) -> list[ExtractedItem]:
        raise NotImplementedError("小红书专用解析器将在后续版本提供")

    def download(
        self,
        url: str,
        dest_dir: Path,
        options: BatchOptions,
        runner: CommandRunner,
    ) -> dict:
        raise NotImplementedError("小红书专用解析器将在后续版本提供")
