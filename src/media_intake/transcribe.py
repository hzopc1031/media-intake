from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TranscriptResult:
    srt_path: Path
    text_path: Path
    engine: str
    language: str | None = None


@runtime_checkable
class Transcriber(Protocol):
    """后续版本接入 faster-whisper 等离线转写时实现此接口。

    第一步只定义钩子，不实现具体引擎。流水线在没有来源字幕时
    才会调用 transcribe。
    """

    def available(self) -> bool:
        """依赖是否已安装并可运行。"""

    def transcribe(self, media_path: Path, dest_dir: Path) -> TranscriptResult:
        """把音视频转写成 dest_dir 下的 SRT 和纯文本。"""


def resolve_transcriber(name: str | None) -> Transcriber | None:
    if name is None or name in {"", "none", "off"}:
        return None
    raise NotImplementedError(
        f"转写引擎 {name!r} 将在后续版本提供（计划支持 faster-whisper）"
    )
