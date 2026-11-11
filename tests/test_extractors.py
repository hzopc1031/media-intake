from pathlib import Path

from media_intake.extractors import host_matches, url_host
from media_intake.extractors.douyin import DouyinBackend, matches as douyin_matches
from media_intake.extractors.xiaohongshu import XiaohongshuBackend, matches as xhs_matches
from media_intake.extractors.ytdlp import parse_flat_entries, resolve_entry_url


def test_site_backend_host_matching() -> None:
    assert douyin_matches("https://v.douyin.com/abc/")
    assert douyin_matches("https://www.iesdouyin.com/share/video/1")
    assert not douyin_matches("https://example.com/x")
    assert xhs_matches("https://www.xiaohongshu.com/explore/1")
    assert xhs_matches("https://xhslink.com/abc")
    assert DouyinBackend().matches("https://www.douyin.com/video/1")
    assert XiaohongshuBackend().matches("https://www.xiaohongshu.com/explore/1")


def test_host_matches() -> None:
    assert host_matches(url_host("https://www.douyin.com/a"), ["douyin.com"])
    assert not host_matches("notdouyin.com", ["douyin.com"])


def test_parse_flat_entries_and_id_fallback() -> None:
    stdout = "\n".join(
        [
            "WARNING: ignore me",
            '{"id": "aaa", "webpage_url": "https://example.com/v/aaa", "title": "A", "duration": 10}',
            '{"id": "bbb", "url": "https://example.com/v/bbb", "title": "B"}',
            "not json",
            '{"id": "ccc", "ie_key": "Youtube", "title": "C"}',
        ]
    )
    items = parse_flat_entries(stdout)
    assert [item.url for item in items] == [
        "https://example.com/v/aaa",
        "https://example.com/v/bbb",
        "https://www.youtube.com/watch?v=ccc",
    ]
    assert items[0].title == "A"
    assert items[0].duration_seconds == 10
    limited = parse_flat_entries(stdout, max_items=1)
    assert len(limited) == 1


def test_resolve_entry_url_prefers_webpage_url() -> None:
    assert (
        resolve_entry_url({"webpage_url": "https://ex.com/a", "url": "https://ex.com/b"})
        == "https://ex.com/a"
    )
    assert resolve_entry_url({"id": "BV1xx", "extractor": "BiliBili"}) == (
        "https://www.bilibili.com/video/BV1xx"
    )
    assert resolve_entry_url({"id": "nope", "ie_key": "UnknownIE"}) is None
