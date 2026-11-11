from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from media_intake.config import BatchOptions
from media_intake.errors import DependencyError
from media_intake.pipeline import run_batch
from media_intake.transcribe import TranscriptResult, resolve_transcriber
from media_intake.whisperx_backend import (
    DIARIZE_TOKEN_HINT,
    WHISPERX_INSTALL_HINT,
    WhisperXTranscriber,
    hf_token,
    segments_to_srt,
    segments_to_text,
    whisperx_is_installed,
)
from tests.fakes import FakeRunner


def test_diarize_requires_whisperx_backend(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="whisperx"):
        BatchOptions(library_root=tmp_path, diarize=True, asr_backend="faster-whisper")
    BatchOptions(library_root=tmp_path, diarize=True, asr_backend="whisperx")


def test_hf_token_reads_known_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("MEDIA_INTAKE_HF_TOKEN", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    assert hf_token() is None
    monkeypatch.setenv("HF_TOKEN", " secret-token ")
    assert hf_token() == "secret-token"
    # 不要在断言里误用令牌值以外的泄漏检查：函数返回值仅在测试中使用。
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("MEDIA_INTAKE_HF_TOKEN", "another")
    assert hf_token() == "another"


def test_segments_include_speaker_labels() -> None:
    segments = [
        {
            "start": 0.0,
            "end": 1.2,
            "text": "Hello world",
            "speaker": "SPEAKER_00",
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.4, "score": 0.9, "speaker": "SPEAKER_00"},
                {"word": "world", "start": 0.4, "end": 1.2, "score": 0.8, "speaker": "SPEAKER_00"},
            ],
        },
        {
            "start": 1.2,
            "end": 2.0,
            "text": "Thanks",
            "speaker": "SPEAKER_01",
            "words": [{"word": "Thanks", "start": 1.2, "end": 2.0, "speaker": "SPEAKER_01"}],
        },
    ]
    srt = segments_to_srt(segments, with_speakers=True)
    assert "[SPEAKER_00] Hello world" in srt
    assert "[SPEAKER_01] Thanks" in srt
    text = segments_to_text(segments, with_speakers=True)
    assert text.splitlines()[0] == "[SPEAKER_00] Hello world"


def test_whisperx_transcriber_writes_words_and_speakers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "language": "en",
        "diarized": True,
        "segments": [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "Hello there",
                "speaker": "SPEAKER_00",
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 0.4, "speaker": "SPEAKER_00"},
                    {"word": "there", "start": 0.4, "end": 1.0, "speaker": "SPEAKER_00"},
                ],
            }
        ],
    }
    monkeypatch.setattr("media_intake.whisperx_backend.whisperx_is_installed", lambda: True)
    monkeypatch.setattr("media_intake.whisperx_backend.hf_token", lambda: "test-token")
    monkeypatch.setattr(
        "media_intake.whisperx_backend.transcribe_with_whisperx",
        lambda *args, **kwargs: payload,
    )
    media = tmp_path / "talk.wav"
    media.write_bytes(b"xx")
    dest = tmp_path / "out"
    result = WhisperXTranscriber(model="tiny", language="en", diarize=True).transcribe(media, dest)
    assert result.engine == "whisperx"
    assert result.diarized is True
    assert result.words_path is not None
    assert "[SPEAKER_00] Hello there" in result.srt_path.read_text(encoding="utf-8")
    assert "[SPEAKER_00] Hello there" in result.text_path.read_text(encoding="utf-8")
    words = json.loads(result.words_path.read_text(encoding="utf-8"))
    assert words["engine"] == "whisperx"
    assert words["diarized"] is True
    assert words["segments"][0]["words"][0]["word"] == "Hello"


def test_whisperx_diarize_without_token_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("media_intake.whisperx_backend.whisperx_is_installed", lambda: True)
    monkeypatch.setattr("media_intake.whisperx_backend.hf_token", lambda: None)
    media = tmp_path / "talk.wav"
    media.write_bytes(b"xx")
    with pytest.raises(DependencyError, match="Hugging Face"):
        WhisperXTranscriber(diarize=True).transcribe(media, tmp_path / "out")
    assert "MEDIA_INTAKE_HF_TOKEN" in DIARIZE_TOKEN_HINT


def test_missing_whisperx_package_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("media_intake.whisperx_backend.whisperx_is_installed", lambda: False)
    with pytest.raises(DependencyError, match="media-intake\\[whisperx\\]"):
        resolve_transcriber("whisperx")
    assert "pip install" in WHISPERX_INSTALL_HINT


def test_pipeline_records_whisperx_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Wx:
        def available(self) -> bool:
            return True

        def transcribe(self, media_path: Path, dest_dir: Path) -> TranscriptResult:
            dest_dir.mkdir(parents=True, exist_ok=True)
            srt = dest_dir / "transcript.srt"
            txt = dest_dir / "transcript.txt"
            words = dest_dir / "transcript.words.json"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n[SPEAKER_00] hello\n",
                encoding="utf-8",
            )
            txt.write_text("[SPEAKER_00] hello\n", encoding="utf-8")
            words.write_text(
                json.dumps({"engine": "whisperx", "diarized": True, "segments": []}) + "\n",
                encoding="utf-8",
            )
            return TranscriptResult(
                srt_path=srt,
                text_path=txt,
                engine="whisperx",
                language="en",
                model="tiny",
                words_path=words,
                diarized=True,
            )

    media = tmp_path / "talk.mp4"
    media.write_bytes(b"x")
    options = BatchOptions(
        library_root=tmp_path / "lib",
        retries=0,
        transcribe_mode="always",
        asr_backend="whisperx",
        transcriber=_Wx(),  # type: ignore[arg-type]
    )
    report = run_batch([str(media)], options, runner=FakeRunner())
    assert report.exit_code == 0
    job = report.succeeded[0]
    assert job.transcript_engine == "whisperx"
    assert job.transcript_diarized is True
    assert job.transcript_words_path and job.transcript_words_path.endswith("transcript.words.json")
    rec = json.loads((options.library_root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["transcript_engine"] == "whisperx"
    assert rec["transcript_diarized"] is True
    assert (options.library_root / rec["transcript_words_path"]).is_file()


@pytest.mark.whisperx
def test_optional_real_whisperx_stack() -> None:
    if os.environ.get("MEDIA_INTAKE_WHISPERX") != "1":
        pytest.skip("set MEDIA_INTAKE_WHISPERX=1 才会运行真实 whisperX 测试")
    if not whisperx_is_installed():
        pytest.skip("未安装 whisperx")
