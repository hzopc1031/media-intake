from __future__ import annotations

from pathlib import Path

from media_intake.extractors.base import (
    BackendRegistry,
    ExtractedItem,
    MediaBackend,
    host_matches,
    url_host,
)
from media_intake.extractors.ytdlp import YtDlpBackend

__all__ = [
    "BackendRegistry",
    "ExtractedItem",
    "MediaBackend",
    "YtDlpBackend",
    "default_registry",
    "host_matches",
    "url_host",
]


def default_registry(ytdlp: Path) -> BackendRegistry:
    """返回本版本启用的后端。

    站点专用后端（见 douyin.py、xiaohongshu.py）实现后，应插入到
    YtDlpBackend 之前，以便按 URL 优先选用。当前未注册它们，相关链接
    仍走 yt-dlp。
    """
    return BackendRegistry([YtDlpBackend(ytdlp)])
