from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable
from urllib.parse import urlparse

from media_intake.config import BatchOptions
from media_intake.errors import JobError
from media_intake.process import CommandRunner


@dataclass(frozen=True)
class ExtractedItem:
    """播放列表或主页展开后的单条媒体。"""

    url: str
    title: str | None = None
    duration_seconds: float | None = None


@runtime_checkable
class MediaBackend(Protocol):
    """站点提取 / 下载后端。

    默认实现是 yt-dlp。抖音、小红书等站点专用后端按 URL 匹配，
    并必须排在默认后端之前注册；本版本尚未实现这些专用后端。
    """

    name: str

    def matches(self, url: str) -> bool:
        """该后端是否声称能处理这个链接。"""

    def expand(
        self,
        url: str,
        options: BatchOptions,
        runner: CommandRunner,
        *,
        max_items: int | None,
    ) -> list[ExtractedItem]:
        """把播放列表或创作者主页展开成单条链接。单条视频应返回它自己。"""

    def download(
        self,
        url: str,
        dest_dir: Path,
        options: BatchOptions,
        runner: CommandRunner,
    ) -> dict:
        """把媒体和来源字幕写入 dest_dir，返回信息字典（标题、时长等）。"""


class BackendRegistry:
    """按注册顺序选择第一个 matches(url) 为真的后端。请把专用后端放在默认后端前面。"""

    def __init__(self, backends: Sequence[MediaBackend]) -> None:
        self.backends = list(backends)
        if not self.backends:
            raise ValueError("至少需要注册一个媒体后端")

    def choose(self, url: str) -> MediaBackend:
        for backend in self.backends:
            if backend.matches(url):
                return backend
        raise JobError(f"没有后端能处理该链接：{url}")


def url_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def host_matches(host: str, suffixes: Sequence[str]) -> bool:
    host = host.lower().rstrip(".")
    for suffix in suffixes:
        suffix = suffix.lower()
        if host == suffix or host.endswith("." + suffix):
            return True
    return False
