from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from media_intake.cli import main

pytestmark = pytest.mark.online


def test_optional_real_platform_download(tmp_path: Path) -> None:
    """可选：对真实平台做一次下载。默认和 CI 不会跑。

    需要同时设置：
      MEDIA_INTAKE_ONLINE=1
      MEDIA_INTAKE_ONLINE_URL=<你有权下载的链接>
    """
    if os.environ.get("MEDIA_INTAKE_ONLINE") != "1":
        pytest.skip("set MEDIA_INTAKE_ONLINE=1 才会运行联网测试")
    url = os.environ.get("MEDIA_INTAKE_ONLINE_URL", "").strip()
    if not url:
        pytest.skip("set MEDIA_INTAKE_ONLINE_URL 为一条你有权下载的链接")
    library = tmp_path / "lib"
    code = main(["run", url, "--library", str(library), "--retries", "1", "--retry-wait", "1"])
    assert code == 0, "联网下载失败，请确认该链接允许下载且本机 yt-dlp 支持"
    records = [
        json.loads(line)
        for line in (library / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert records and records[0]["status"] == "succeeded"
    assert (library / records[0]["media_path"]).is_file()
