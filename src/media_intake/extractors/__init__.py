from __future__ import annotations

from pathlib import Path

from media_intake.extractors.base import (
    BackendRegistry,
    ExtractedItem,
    MediaBackend,
    host_matches,
    url_host,
)
from media_intake.extractors.douyin import DouyinBackend
from media_intake.extractors.fallback import FallbackBackend
from media_intake.extractors.xiaohongshu import XiaohongshuBackend
from media_intake.extractors.ytdlp import YtDlpBackend

__all__ = [
    "BackendRegistry",
    "DouyinBackend",
    "ExtractedItem",
    "FallbackBackend",
    "MediaBackend",
    "XiaohongshuBackend",
    "YtDlpBackend",
    "default_registry",
    "host_matches",
    "url_host",
]


def default_registry(ytdlp: Path) -> BackendRegistry:
    """按 URL 选择后端：抖音、小红书专用解析器在前，yt-dlp 作为默认和回退。"""
    ytdlp_backend = YtDlpBackend(ytdlp)
    return BackendRegistry(
        [
            FallbackBackend(DouyinBackend(), ytdlp_backend),
            FallbackBackend(XiaohongshuBackend(), ytdlp_backend),
            ytdlp_backend,
        ]
    )
