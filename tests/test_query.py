from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_intake.errors import InputError, JobError
from media_intake.metadata import fields_from_info, get_metadata, probe_source
from media_intake.query import read_item, read_transcript, search_library
from media_intake.process import CommandResult


def _write_library(root: Path) -> str:
    item_id = "aabbccddeeff"
    item_dir = root / "items" / item_id
    item_dir.mkdir(parents=True)
    (item_dir / "source.txt").write_text("来源字幕正文。Hello source.\n", encoding="utf-8")
    (item_dir / "source.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n来源字幕正文。Hello source.\n",
        encoding="utf-8",
    )
    long = "ABCDEFGHIJ" + "KLMNOPQRST" + "关键词在中间。" + "尾部内容。" * 20
    (item_dir / "transcript.txt").write_text(long, encoding="utf-8")
    (item_dir / "transcript.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n转写正文。\n",
        encoding="utf-8",
    )
    rec = {
        "id": item_id,
        "source": "https://example.com/talk",
        "source_kind": "url",
        "title": "测试讲座",
        "duration_seconds": 12.5,
        "status": "succeeded",
        "item_dir": f"items/{item_id}",
        "media_path": f"items/{item_id}/media.mp4",
        "subtitle_path": f"items/{item_id}/source.srt",
        "subtitle_text_path": f"items/{item_id}/source.txt",
        "subtitle_kind": "source",
        "transcript_path": f"items/{item_id}/transcript.srt",
        "transcript_text_path": f"items/{item_id}/transcript.txt",
        "transcript_model": "base",
        "transcript_language": "zh",
        "transcript_engine": "faster-whisper",
        "uploader": "示例作者",
        "platform": "Example",
    }
    (root / "manifest.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    return item_id


def test_read_item_and_paginated_transcript(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    item_id = _write_library(library)
    item = read_item(str(library), item_id)
    assert item["kind"] == "item"
    assert item["title"] == "测试讲座"
    assert item["has_source_text"] is True
    assert item["has_transcript"] is True
    assert item["transcript_text_length"] > item["source_text_length"] > 0
    first = read_transcript(str(library), item_id, kind="transcript", offset=0, limit=10)
    assert first["text_kind"] == "transcript"
    assert first["offset"] == 0
    assert first["limit"] == 10
    assert first["returned_length"] == 10
    assert first["total_length"] == item["transcript_text_length"]
    assert first["next_offset"] == 10
    assert first["text"] == "ABCDEFGHIJ"
    assert first["language"] == "zh"
    assert first["model"] == "base"
    second = read_transcript(str(library), item_id, kind="transcript", offset=first["next_offset"], limit=10)
    assert second["offset"] == 10
    assert second["text"] == "KLMNOPQRST"
    source = read_transcript(str(library), item_id, kind="source")
    assert source["text_kind"] == "source"
    assert "Hello source" in source["text"]
    assert source["next_offset"] is None
    auto = read_transcript(str(library), item_id, kind="auto", limit=8)
    assert auto["text_kind"] == "transcript"
    with pytest.raises(InputError, match="offset"):
        read_transcript(str(library), item_id, offset=-1)
    with pytest.raises(InputError, match="limit"):
        read_transcript(str(library), item_id, limit=0)
    with pytest.raises(InputError, match="找不到"):
        read_item(str(library), "000000000000")


def test_read_transcript_prefers_source_when_requested(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    item_id = "0123456789ab"
    item_dir = library / "items" / item_id
    item_dir.mkdir(parents=True)
    (item_dir / "source.txt").write_text("只有来源字幕\n", encoding="utf-8")
    rec = {
        "id": item_id,
        "source": "clip.mp4",
        "source_kind": "local",
        "title": "本地",
        "status": "succeeded",
        "subtitle_text_path": f"items/{item_id}/source.txt",
    }
    (library / "manifest.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    auto = read_transcript(str(library), item_id, kind="auto")
    assert auto["text_kind"] == "source"
    assert "只有来源字幕" in auto["text"]
    with pytest.raises(InputError, match="没有可读取的正文"):
        read_transcript(str(library), item_id, kind="transcript")
    past_end = read_transcript(str(library), item_id, kind="source", offset=1000, limit=10)
    assert past_end["text"] == ""
    assert past_end["total_length"] > 0
    assert past_end["next_offset"] is None


def test_search_library_title_and_text(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    item_id = _write_library(library)
    by_title = search_library(str(library), "讲座")
    assert by_title["count"] == 1
    assert by_title["records"][0]["id"] == item_id
    assert "title" in by_title["records"][0]["matched_in"]
    by_text = search_library(str(library), "关键词在中间")
    assert by_text["count"] == 1
    assert by_text["records"][0]["matched_in"] == ["transcript"]
    assert "关键词在中间" in (by_text["records"][0]["snippet"] or "")
    none = search_library(str(library), "不会出现的句子")
    assert none["count"] == 0
    with pytest.raises(InputError, match="检索词"):
        search_library(str(library), "  ")
    empty = search_library(str(tmp_path / "missing"), "讲座")
    assert empty["count"] == 0


def test_fields_from_info() -> None:
    fields = fields_from_info(
        {
            "extractor_key": "Youtube",
            "channel": "Example Channel",
            "upload_date": "20260102",
            "subtitles": {"en": []},
            "automatic_captions": {},
        }
    )
    assert fields["platform"] == "Youtube"
    assert fields["uploader"] == "Example Channel"
    assert fields["upload_date"] == "20260102"


class _ProbeRunner:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[list[str]] = []

    def run(self, argv, *, cwd=None, timeout=None) -> CommandResult:
        argv = list(argv)
        self.calls.append(argv)
        if argv[-1] in {"--version", "-version"}:
            return CommandResult(argv, 0, "2026.10.26\n", "")
        if "--dump-json" in argv:
            return CommandResult(argv, 0, json.dumps(self.payload) + "\n", "")
        return CommandResult(argv, 1, "", "unexpected")


def test_probe_url_without_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from media_intake import metadata as metadata_mod

    monkeypatch.setattr(metadata_mod, "require_ytdlp", lambda runner=None: Path("/usr/bin/yt-dlp"))
    runner = _ProbeRunner(
        {
            "title": "Open Example",
            "duration": 45,
            "extractor_key": "Generic",
            "uploader": "Ada",
            "upload_date": "20261111",
            "subtitles": {"en": [{"ext": "srt"}]},
            "automatic_captions": {},
            "is_live": False,
        }
    )
    result = probe_source("https://example.com/watch/1", runner=runner)
    assert result["kind"] == "probe"
    assert result["title"] == "Open Example"
    assert result["duration_seconds"] == 45
    assert result["has_subtitles"] is True
    assert result["has_automatic_captions"] is False
    assert result["uploader"] == "Ada"
    assert result["platform"] == "Generic"
    assert any("--skip-download" in call and "--dump-json" in call for call in runner.calls)
    assert all(call[-1] == "https://example.com/watch/1" or call[-1] in {"--version", "-version"} for call in runner.calls)


def test_get_metadata_prefers_library_item(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    item_id = _write_library(library)
    rec = get_metadata(item_id, library=str(library))
    assert rec["kind"] == "item"
    assert rec["title"] == "测试讲座"


def test_probe_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from media_intake import metadata as metadata_mod

    monkeypatch.setattr(metadata_mod, "require_ytdlp", lambda runner=None: Path("/usr/bin/yt-dlp"))

    class Bad:
        def run(self, argv, *, cwd=None, timeout=None) -> CommandResult:
            return CommandResult(list(argv), 1, "", "HTTP 404")

    with pytest.raises(JobError, match="探测元数据失败"):
        probe_source("https://example.com/missing", runner=Bad())
