from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from media_intake.cli import main

pytestmark = pytest.mark.online


def _require_online() -> None:
    if os.environ.get("MEDIA_INTAKE_ONLINE") != "1":
        pytest.skip("set MEDIA_INTAKE_ONLINE=1 才会运行联网测试")


def _run(url: str, library: Path) -> tuple[int, list[dict]]:
    code = main(["run", url, "--library", str(library), "--retries", "0"])
    manifest = library / "manifest.jsonl"
    records = []
    if manifest.is_file():
        records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line]
    return code, records


def test_optional_real_platform_download(tmp_path: Path) -> None:
    """可选：对任意获准下载的链接做一次下载。"""
    _require_online()
    url = os.environ.get("MEDIA_INTAKE_ONLINE_URL", "").strip()
    if not url:
        pytest.skip("set MEDIA_INTAKE_ONLINE_URL 为一条你有权下载的链接")
    code, records = _run(url, tmp_path / "lib")
    assert code == 0, "联网下载失败，请确认该链接允许下载"
    assert records and records[0]["status"] == "succeeded"
    assert (tmp_path / "lib" / records[0]["media_path"]).is_file()


def test_optional_douyin_public_url(tmp_path: Path) -> None:
    """可选：对一条公开抖音视频链接运行专用后端（失败时保留报告，不把反爬当成测试夹具）。"""
    _require_online()
    url = os.environ.get("MEDIA_INTAKE_ONLINE_DOUYIN_URL", "").strip()
    if not url:
        pytest.skip("set MEDIA_INTAKE_ONLINE_DOUYIN_URL 为一条公开抖音视频链接")
    code, records = _run(url, tmp_path / "douyin-lib")
    if code != 0 or not records or records[0]["status"] != "succeeded":
        reason = records[0]["error"] if records else f"exit={code}"
        pytest.fail(f"抖音联网测试未成功：{reason}")
    assert (tmp_path / "douyin-lib" / records[0]["media_path"]).is_file()


def test_optional_xiaohongshu_public_url(tmp_path: Path) -> None:
    """可选：对一条公开小红书笔记链接运行专用后端。"""
    _require_online()
    url = os.environ.get("MEDIA_INTAKE_ONLINE_XHS_URL", "").strip()
    if not url:
        pytest.skip("set MEDIA_INTAKE_ONLINE_XHS_URL 为一条公开小红书笔记链接")
    code, records = _run(url, tmp_path / "xhs-lib")
    if code != 0 or not records or records[0]["status"] != "succeeded":
        reason = records[0]["error"] if records else f"exit={code}"
        pytest.fail(f"小红书联网测试未成功：{reason}")
    assert (tmp_path / "xhs-lib" / records[0]["media_path"]).is_file()
