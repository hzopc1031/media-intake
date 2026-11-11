from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT_TMP_KEY = "media-intake-tests"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="需要本机 ffmpeg/ffprobe")


def write_sample_srt(path: Path, text: str = "你好，这是测试字幕。") -> Path:
    path.write_text(
        "1\n"
        "00:00:00,000 --> 00:00:01,000\n"
        f"{text}\n",
        encoding="utf-8",
    )
    return path


def generate_sample_media(dest: Path, *, duration: float = 2.0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate=10:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    subprocess.run(cmd, check=True)
    return dest


def flite_available() -> bool:
    if not ffmpeg_available():
        return False
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        check=False,
        capture_output=True,
        text=True,
    )
    return "flite" in (result.stdout or "")


requires_flite = pytest.mark.skipif(not flite_available(), reason="本机 ffmpeg 未启用 flite 滤镜")


def generate_speech_wav(dest: Path, text: str = "The water is blue today.") -> Path:
    """用 ffmpeg 的 flite 滤镜合成一句英语语音，不依赖网络。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    safe = text.replace("\\", " ").replace("'", " ").replace(":", " ").replace(",", " ")
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"flite=text='{safe}':voice=slt",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(dest),
    ]
    subprocess.run(cmd, check=True)
    return dest
