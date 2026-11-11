from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from media_intake.cli import main
from media_intake.transcribe import whisper_is_installed
from tests.conftest import generate_speech_wav, requires_ffmpeg, requires_flite

pytestmark = pytest.mark.slow


def _tiny_model_cached() -> bool:
    try:
        from faster_whisper import WhisperModel

        WhisperModel("tiny", device="cpu", compute_type="int8", local_files_only=True)
        return True
    except Exception:
        return False


def _should_run_real_whisper() -> str | None:
    if not whisper_is_installed():
        return "未安装 faster-whisper"
    if os.environ.get("MEDIA_INTAKE_WHISPER") == "1" or _tiny_model_cached():
        return None
    return "需要 tiny 模型（设置 MEDIA_INTAKE_WHISPER=1 允许下载，或预先缓存模型）"


@requires_ffmpeg
@requires_flite
def test_e2e_tiny_whisper_on_generated_speech(tmp_path: Path) -> None:
    reason = _should_run_real_whisper()
    if reason:
        pytest.skip(reason)
    media = tmp_path / "speech.wav"
    generate_speech_wav(media, "The water is blue today.")
    library = tmp_path / "lib"
    code = main(
        [
            "run",
            str(media),
            "--library",
            str(library),
            "--transcribe",
            "--whisper-model",
            "tiny",
            "--whisper-language",
            "en",
            "--whisper-device",
            "cpu",
            "--retries",
            "0",
        ]
    )
    assert code == 0
    records = [
        json.loads(line)
        for line in (library / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(records) == 1
    rec = records[0]
    assert rec["status"] == "succeeded"
    assert rec["transcript_path"] == f"items/{rec['id']}/transcript.srt"
    assert rec["transcript_text_path"] == f"items/{rec['id']}/transcript.txt"
    assert rec["transcript_model"] == "tiny"
    text = (library / rec["transcript_text_path"]).read_text(encoding="utf-8").lower()
    srt = (library / rec["transcript_path"]).read_text(encoding="utf-8")
    assert "-->" in srt
    # tiny 模型对合成语音不一定字字准确，至少应识别出这句里的关键词
    assert "water" in text or "blue" in text
