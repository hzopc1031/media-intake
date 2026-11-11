from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from media_intake.transcribe import TranscribeMode, Transcriber, parse_transcribe_mode


@dataclass
class BatchOptions:
    """一批任务共用的选项。后续 MCP 服务与命令行共用此结构。"""

    library_root: Path
    concurrency: int = 2
    retries: int = 3
    retry_wait: float = 1.0
    format: str | None = None
    audio_only: bool = False
    write_subs: bool = True
    sub_langs: str | None = None
    write_auto_subs: bool = False
    cookies_file: Path | None = None
    cookies_from_browser: str | None = None
    extra_ytdlp_args: list[str] = field(default_factory=list)
    force: bool = False
    reference_local: bool = False
    expand: bool = False
    max_items: int | None = None
    transcriber: Transcriber | None = None
    transcribe_mode: TranscribeMode | str = TranscribeMode.NEVER
    whisper_model: str = "base"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    whisper_language: str | None = None
    asr_backend: str = "faster-whisper"
    diarize: bool = False
    cancel_event: object | None = None

    def __post_init__(self) -> None:
        self.library_root = Path(self.library_root).expanduser().resolve()
        if self.concurrency < 1:
            raise ValueError("并发数必须大于等于 1")
        if self.retries < 0:
            raise ValueError("重试次数不能为负数")
        if self.retry_wait < 0:
            raise ValueError("退避等待不能为负数")
        if self.cookies_file is not None:
            self.cookies_file = Path(self.cookies_file).expanduser()
        if self.cookies_from_browser is not None:
            self.cookies_from_browser = self.cookies_from_browser.strip() or None
        if self.max_items is not None and self.max_items < 1:
            raise ValueError("展开条数上限 --max-items 必须大于等于 1")
        if self.max_items is not None and not self.expand:
            raise ValueError("--max-items 只能与 --expand / --playlist 一起使用")
        self.transcribe_mode = parse_transcribe_mode(self.transcribe_mode)
        self.whisper_model = (self.whisper_model or "base").strip() or "base"
        self.whisper_device = (self.whisper_device or "auto").strip() or "auto"
        self.whisper_compute_type = (self.whisper_compute_type or "auto").strip() or "auto"
        if self.whisper_language is not None:
            lang = self.whisper_language.strip()
            self.whisper_language = None if not lang or lang.lower() == "auto" else lang
        backend = (self.asr_backend or "faster-whisper").strip().lower().replace("_", "-")
        if backend in {"whisper", "faster-whisper", "fasterwhisper"}:
            backend = "faster-whisper"
        elif backend in {"whisperx", "whisper-x"}:
            backend = "whisperx"
        else:
            raise ValueError("转写后端 --asr-backend 必须是 faster-whisper 或 whisperx")
        self.asr_backend = backend
        self.diarize = bool(self.diarize)
