from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from media_intake.errors import DependencyError, JobError
from media_intake.subtitles import write_plain_text

WHISPER_INSTALL_HINT = (
    "未安装 faster-whisper，无法离线转写。这是可选功能，请先安装：\n"
    f"  {sys.executable} -m pip install 'media-intake[whisper]'\n"
    "或：\n"
    f"  {sys.executable} -m pip install faster-whisper"
)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".heic", ".heif"}


class TranscribeMode(str, Enum):
    NEVER = "never"
    MISSING = "missing"
    ALWAYS = "always"


def parse_transcribe_mode(value: str | TranscribeMode) -> TranscribeMode:
    text = value.value if isinstance(value, TranscribeMode) else str(value).strip().lower()
    try:
        return TranscribeMode(text)
    except ValueError as exc:
        raise ValueError("转写模式 --transcribe 必须是 never、missing 或 always") from exc


@dataclass(frozen=True)
class TranscriptResult:
    srt_path: Path
    text_path: Path
    engine: str
    language: str | None = None
    model: str | None = None
    words_path: Path | None = None
    diarized: bool = False


@runtime_checkable
class Transcriber(Protocol):
    """离线转写引擎接口。流水线按转写模式在需要时调用 transcribe。"""

    def available(self) -> bool:
        """依赖是否已安装并可运行。"""

    def transcribe(self, media_path: Path, dest_dir: Path) -> TranscriptResult:
        """把音视频转写成 dest_dir 下的 SRT 和纯文本。"""


def whisper_is_installed() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


class FasterWhisperTranscriber:
    """使用 faster-whisper 的离线转写实现。模型在首次转写时加载。"""

    def __init__(
        self,
        *,
        model: str = "base",
        device: str = "auto",
        compute_type: str = "auto",
        language: str | None = None,
    ) -> None:
        self.model_name = model
        self.device_spec = device or "auto"
        self.compute_type_spec = compute_type or "auto"
        self.language = language or None
        self._model = None
        self._resolved_device: str | None = None
        self._resolved_compute: str | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return whisper_is_installed()

    def transcribe(self, media_path: Path, dest_dir: Path) -> TranscriptResult:
        if not self.available():
            raise DependencyError(WHISPER_INSTALL_HINT)
        dest_dir.mkdir(parents=True, exist_ok=True)
        model = self._load_model()
        # 用本机 ffmpeg 解码成 PCM，避免依赖 PyAV 打开文件的参数（部分 av 版本不接受 metadata_errors）。
        audio = decode_media_to_pcm(media_path)
        kwargs: dict = {"vad_filter": True}
        if self.language:
            kwargs["language"] = self.language
        try:
            with self._lock:
                segments, info = model.transcribe(audio, **kwargs)
                collected = list(segments)
        except Exception as exc:
            raise JobError(f"faster-whisper 转写失败：{type(exc).__name__}: {exc}") from exc
        language = getattr(info, "language", None) or self.language
        srt_path = dest_dir / "transcript.srt"
        text_path = dest_dir / "transcript.txt"
        srt_path.write_text(segments_to_srt(collected), encoding="utf-8")
        write_plain_text(srt_path, text_path)
        return TranscriptResult(
            srt_path=srt_path,
            text_path=text_path,
            engine="faster-whisper",
            language=str(language) if language else None,
            model=self.model_name,
        )

    def _load_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            from faster_whisper import WhisperModel

            device = resolve_device(self.device_spec)
            compute = resolve_compute_type(self.compute_type_spec, device)
            try:
                self._model = WhisperModel(self.model_name, device=device, compute_type=compute)
            except Exception as exc:
                raise JobError(
                    "加载 faster-whisper 模型失败"
                    f"（model={self.model_name}，device={device}，compute_type={compute}）："
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            self._resolved_device = device
            self._resolved_compute = compute
            return self._model



def decode_media_to_pcm(media_path: Path, *, sample_rate: int = 16000):
    """把音视频解成 16 kHz 单声道 float32 PCM，返回 numpy 数组。"""
    import numpy as np

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise JobError("转写需要本机 ffmpeg 来解码音频")
    argv = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(media_path),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "pipe:1",
    ]
    result = subprocess.run(argv, capture_output=True, check=False)
    if result.returncode != 0 or not result.stdout:
        err = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise JobError(f"ffmpeg 解码音频失败：{err or '没有音频输出'}")
    return np.frombuffer(result.stdout, dtype=np.float32).copy()


def resolve_device(spec: str) -> str:
    text = (spec or "auto").strip().lower()
    if text != "auto":
        return text
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def resolve_compute_type(spec: str, device: str) -> str:
    text = (spec or "auto").strip().lower()
    if text != "auto":
        return text
    return "float16" if device == "cuda" else "int8"


def segments_to_srt(segments: Iterable[object]) -> str:
    lines: list[str] = []
    index = 1
    for segment in segments:
        text = str(getattr(segment, "text", "") or "").strip()
        if not text:
            continue
        start = format_srt_timestamp(getattr(segment, "start", 0) or 0)
        end = format_srt_timestamp(getattr(segment, "end", 0) or 0)
        if end <= start:
            end = format_srt_timestamp((getattr(segment, "start", 0) or 0) + 0.5)
        lines.append(str(index))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
        index += 1
    return "\n".join(lines) + ("\n" if lines else "")


def format_srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    millis = int(round(float(seconds) * 1000))
    hours, millis = divmod(millis, 3600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def is_image_media(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_SUFFIXES


def should_transcribe(
    mode: TranscribeMode | str,
    *,
    has_source_subs: bool,
    media_path: Path | None,
) -> bool:
    parsed = parse_transcribe_mode(mode)
    if parsed is TranscribeMode.NEVER:
        return False
    if media_path is not None and is_image_media(media_path):
        return False
    if parsed is TranscribeMode.ALWAYS:
        return True
    return not has_source_subs


def resolve_transcriber(name: str | None) -> Transcriber | None:
    if name is None or name in {"", "none", "off"}:
        return None
    normalized = str(name).strip().lower().replace("_", "-")
    if normalized in {"faster-whisper", "whisper", "fasterwhisper"}:
        if not whisper_is_installed():
            raise DependencyError(WHISPER_INSTALL_HINT)
        return FasterWhisperTranscriber()
    if normalized in {"whisperx", "whisper-x"}:
        from media_intake.whisperx_backend import WHISPERX_INSTALL_HINT, WhisperXTranscriber, whisperx_is_installed

        if not whisperx_is_installed():
            raise DependencyError(WHISPERX_INSTALL_HINT)
        return WhisperXTranscriber()
    raise ValueError(f"不支持的转写引擎：{name}。当前可用：faster-whisper、whisperx")
