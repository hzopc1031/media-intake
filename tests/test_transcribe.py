from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from media_intake.cli import main
from media_intake.config import BatchOptions
from media_intake.errors import DependencyError
from media_intake.pipeline import run_batch
from media_intake.transcribe import (
    FasterWhisperTranscriber,
    TranscribeMode,
    TranscriptResult,
    WHISPER_INSTALL_HINT,
    format_srt_timestamp,
    parse_transcribe_mode,
    resolve_compute_type,
    resolve_device,
    resolve_transcriber,
    segments_to_srt,
    should_transcribe,
)
from tests.fakes import FakeRunner


class _Seg:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _Info:
    language = "en"


class _FakeModel:
    def transcribe(self, audio, **kwargs: object):
        assert audio is not None
        return iter([_Seg(0.0, 1.2, "Hello world"), _Seg(1.2, 2.0, "The water is blue")]), _Info()


class _DummyTranscriber:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def available(self) -> bool:
        return True

    def transcribe(self, media_path: Path, dest_dir: Path) -> TranscriptResult:
        self.calls.append(media_path)
        srt = dest_dir / "transcript.srt"
        txt = dest_dir / "transcript.txt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
        txt.write_text("hello\n", encoding="utf-8")
        return TranscriptResult(
            srt_path=srt,
            text_path=txt,
            engine="dummy",
            language="en",
            model="dummy",
        )


def test_parse_mode_and_should_transcribe(tmp_path: Path) -> None:
    assert parse_transcribe_mode("MISSING") is TranscribeMode.MISSING
    with pytest.raises(ValueError, match="never、missing 或 always"):
        parse_transcribe_mode("sometimes")
    video = tmp_path / "talk.mp4"
    image = tmp_path / "note.jpg"
    video.write_bytes(b"x")
    image.write_bytes(b"x")
    assert should_transcribe("never", has_source_subs=False, media_path=video) is False
    assert should_transcribe("missing", has_source_subs=True, media_path=video) is False
    assert should_transcribe("missing", has_source_subs=False, media_path=video) is True
    assert should_transcribe("always", has_source_subs=True, media_path=video) is True
    assert should_transcribe("always", has_source_subs=False, media_path=image) is False
    assert should_transcribe("missing", has_source_subs=False, media_path=image) is False


def test_segments_to_srt_and_timestamp() -> None:
    assert format_srt_timestamp(1.2) == "00:00:01,200"
    assert format_srt_timestamp(-1) == "00:00:00,000"
    srt = segments_to_srt([_Seg(0, 1.2, "Hello world"), _Seg(1.2, 1.2, ""), _Seg(1.2, 1.2, "Tail")])
    assert "00:00:00,000 --> 00:00:01,200" in srt
    assert "Hello world" in srt
    assert "00:00:01,200 --> 00:00:01,700" in srt
    assert "Tail" in srt


def test_resolve_device_and_compute_type() -> None:
    assert resolve_device("CPU") == "cpu"
    assert resolve_device("auto") in {"cpu", "cuda"}
    assert resolve_compute_type("auto", "cpu") == "int8"
    assert resolve_compute_type("auto", "cuda") == "float16"
    assert resolve_compute_type("int8_float16", "cpu") == "int8_float16"


def test_faster_whisper_transcriber_writes_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("media_intake.transcribe.whisper_is_installed", lambda: True)
    monkeypatch.setattr(FasterWhisperTranscriber, "_load_model", lambda self: _FakeModel())
    monkeypatch.setattr("media_intake.transcribe.decode_media_to_pcm", lambda path: [0.0])
    media = tmp_path / "clip.wav"
    media.write_bytes(b"xx")
    dest = tmp_path / "out"
    result = FasterWhisperTranscriber(model="tiny", language="en").transcribe(media, dest)
    assert result.engine == "faster-whisper"
    assert result.model == "tiny"
    assert result.language == "en"
    text = result.srt_path.read_text(encoding="utf-8")
    assert "Hello world" in text
    assert "The water is blue" in result.text_path.read_text(encoding="utf-8")


def test_missing_whisper_package_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("media_intake.transcribe.whisper_is_installed", lambda: False)
    with pytest.raises(DependencyError, match="faster-whisper"):
        run_batch(
            [str(tmp_path / "missing.wav")],
            BatchOptions(library_root=tmp_path / "lib", retries=0, transcribe_mode="missing"),
            runner=FakeRunner(),
        )
    with pytest.raises(DependencyError, match="media-intake\\[whisper\\]"):
        resolve_transcriber("faster-whisper")
    assert "pip install" in WHISPER_INSTALL_HINT


def test_cli_transcribe_without_package_prints_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("media_intake.transcribe.whisper_is_installed", lambda: False)
    media = tmp_path / "talk.mp4"
    media.write_bytes(b"x")
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = main(
            ["run", str(media), "--library", str(tmp_path / "lib"), "--transcribe", "--retries", "0"]
        )
    assert code == 2
    assert "faster-whisper" in buf.getvalue()
    assert "media-intake[whisper]" in buf.getvalue()


def test_modes_missing_and_always_with_source_subs(tmp_path: Path) -> None:
    media = tmp_path / "talk.mp4"
    media.write_bytes(b"not-real")
    (tmp_path / "talk.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n来源字幕\n", encoding="utf-8"
    )
    dummy = _DummyTranscriber()
    missing = BatchOptions(
        library_root=tmp_path / "lib-missing",
        retries=0,
        transcribe_mode="missing",
        transcriber=dummy,  # type: ignore[arg-type]
    )
    report = run_batch([str(media)], missing, runner=FakeRunner())
    assert report.exit_code == 0
    assert report.succeeded[0].transcript_path is None
    assert dummy.calls == []

    always_dummy = _DummyTranscriber()
    always = BatchOptions(
        library_root=tmp_path / "lib-always",
        retries=0,
        transcribe_mode="always",
        transcriber=always_dummy,  # type: ignore[arg-type]
    )
    report2 = run_batch([str(media)], always, runner=FakeRunner())
    assert report2.exit_code == 0
    job = report2.succeeded[0]
    assert job.transcript_path and job.transcript_path.endswith("transcript.srt")
    assert job.subtitle_path and job.subtitle_path.endswith("source.srt")
    assert always_dummy.calls
    rec = json.loads((always.library_root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["transcript_model"] == "dummy"
    assert rec["transcript_language"] == "en"


def test_never_skips_even_with_transcriber(tmp_path: Path) -> None:
    media = tmp_path / "talk.mp4"
    media.write_bytes(b"x")
    dummy = _DummyTranscriber()
    report = run_batch(
        [str(media)],
        BatchOptions(
            library_root=tmp_path / "lib",
            retries=0,
            transcribe_mode="never",
            transcriber=dummy,  # type: ignore[arg-type]
        ),
        runner=FakeRunner(),
    )
    assert report.exit_code == 0
    assert report.succeeded[0].transcript_path is None
    assert dummy.calls == []
