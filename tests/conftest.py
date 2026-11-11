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
