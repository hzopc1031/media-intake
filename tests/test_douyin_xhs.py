from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_intake.config import BatchOptions
from media_intake.errors import JobError
from media_intake.extractors import default_registry
from media_intake.extractors.douyin import DouyinBackend, caption_to_srt, classify_douyin_url, matches as douyin_matches
from media_intake.extractors.fallback import FallbackBackend
from media_intake.extractors.httputil import parse_browser_spec
from media_intake.extractors.xiaohongshu import XiaohongshuBackend, classify_xhs_url, matches as xhs_matches
from media_intake.extractors.ytdlp import YtDlpBackend
from tests.fakes import FakeRunner
from tests.fixtures.pages import (
    CAPTION_JSON,
    CAPTION_PAYLOAD,
    EMPTY_HTML,
    FAKE_AWEME_ID,
    FAKE_NOTE_ID,
    IMAGE_1,
    IMAGE_2,
    NWM_VIDEO,
    PLAY_VIDEO,
    PROFILE_NOTE_A,
    PROFILE_VIDEO_A,
    PROFILE_VIDEO_B,
    TINY_JPG,
    TINY_MP4,
    XHS_VIDEO,
    douyin_iteminfo_json,
    douyin_profile_html,
    douyin_render_html,
    xhs_initial_html,
    xhs_profile_html,
)
from tests.http_fakes import MapHttp, bytes_response, html_response, json_response


def _options(tmp_path: Path, **kwargs) -> BatchOptions:
    return BatchOptions(library_root=tmp_path, retries=0, retry_wait=0, **kwargs)


def test_default_registry_prefers_site_backends() -> None:
    registry = default_registry(Path("yt-dlp"))
    assert [b.name for b in registry.backends] == ["douyin", "xiaohongshu", "yt-dlp"]
    assert isinstance(registry.choose("https://www.douyin.com/video/123"), FallbackBackend)
    assert registry.choose("https://www.douyin.com/video/123").name == "douyin"
    assert registry.choose("https://v.douyin.com/AbC/").name == "douyin"
    assert registry.choose("https://www.xiaohongshu.com/explore/abc").name == "xiaohongshu"
    assert registry.choose("https://xhslink.com/o/xyz").name == "xiaohongshu"
    assert registry.choose("https://xhslink.cn/o/xyz").name == "xiaohongshu"
    assert registry.choose("https://www.rednote.com/discovery/item/abc").name == "xiaohongshu"
    assert registry.choose("https://webcast.amemv.com/douyin/webcast/reflow/1").name == "douyin"
    assert isinstance(registry.choose("https://example.com/watch/1"), YtDlpBackend)


def test_matches_share_text_and_short_links() -> None:
    text = "4.52 复制打开抖音，看看视频 https://v.douyin.com/AbCdEf/ 建议看完"
    assert douyin_matches(text)
    assert douyin_matches("https://www.iesdouyin.com/share/video/7000000000000000001")
    assert not douyin_matches("https://example.com/v/1")
    xhs_text = "封面标题 http://xhslink.com/o/Abc123 ，复制本条后打开小红书"
    assert xhs_matches(xhs_text)
    assert xhs_matches("https://www.xiaohongshu.com/explore/aaaaaaaaaaaaaaaaaaaaaaaa")
    assert classify_douyin_url("https://www.douyin.com/user/MS4wLjABAAAA?modal_id=7000000000000000001") == (
        "video",
        "7000000000000000001",
    )
    assert classify_douyin_url("https://www.douyin.com/user/MS4wLjABAAAA")[0] == "profile"
    assert classify_xhs_url("https://www.xiaohongshu.com/user/profile/abcd")[0] == "profile"


def test_parse_browser_spec() -> None:
    assert parse_browser_spec("chrome") == ("chrome", None, None, None)
    assert parse_browser_spec("chrome:Default") == ("chrome", "Default", None, None)
    assert parse_browser_spec("chrome+gnomekeyring:Work::Work")[0] == "chrome"
    assert parse_browser_spec("firefox+gnomekeyring")[2] == "GNOMEKEYRING"


def test_douyin_download_prefers_download_addr_and_writes_subs(tmp_path: Path) -> None:
    share = f"https://v.douyin.com/ShortFake/"
    canonical = f"https://www.iesdouyin.com/share/video/{FAKE_AWEME_ID}/"
    client = MapHttp(
        {
            share: html_response(canonical, douyin_render_html()),
            canonical: html_response(canonical, douyin_render_html()),
            f"https://www.iesdouyin.com/share/video/{FAKE_AWEME_ID}/": html_response(
                canonical, douyin_render_html()
            ),
            NWM_VIDEO: bytes_response(NWM_VIDEO, TINY_MP4),
            PLAY_VIDEO: bytes_response(PLAY_VIDEO, TINY_MP4),
            CAPTION_JSON: json_response(CAPTION_JSON, json.dumps(CAPTION_PAYLOAD, ensure_ascii=False)),
        }
    )
    dest = tmp_path / "item"
    info = DouyinBackend(client=client).download(
        "6.66 复制打开抖音 https://v.douyin.com/ShortFake/ 看看",
        dest,
        _options(tmp_path),
        FakeRunner(),
    )
    assert info["id"] == FAKE_AWEME_ID
    assert info["title"] == "示例视频描述"
    assert info["uploader"] == "示例作者"
    assert info["duration"] == pytest.approx(3.2)
    assert info["backend"] == "douyin"
    assert info["watermark_free"] is True
    assert info["has_source_subs"] is True
    assert (dest / "media.mp4").read_bytes() == TINY_MP4
    assert NWM_VIDEO in client.downloads
    srt = (dest / "source.zh.srt").read_text(encoding="utf-8")
    assert "示例字幕第一句" in srt
    assert "00:00:00,000 --> 00:00:01,200" in srt
    saved = json.loads((dest / "media.info.json").read_text(encoding="utf-8"))
    assert saved["extractor"] == "douyin"


def test_douyin_falls_back_to_play_addr_without_subs(tmp_path: Path) -> None:
    canonical = f"https://www.iesdouyin.com/share/video/{FAKE_AWEME_ID}/"
    html = douyin_render_html(with_caption=False, with_download=False)
    client = MapHttp(
        {
            canonical: html_response(canonical, html),
            f"https://www.iesdouyin.com/share/video/{FAKE_AWEME_ID}/": html_response(canonical, html),
            PLAY_VIDEO: bytes_response(PLAY_VIDEO, TINY_MP4),
        }
    )
    dest = tmp_path / "item"
    info = DouyinBackend(client=client).download(canonical, dest, _options(tmp_path), FakeRunner())
    assert info["watermark_free"] is False
    assert info["has_source_subs"] is False
    assert PLAY_VIDEO in client.downloads
    assert not list(dest.glob("source*.srt"))


def test_douyin_iteminfo_when_html_empty(tmp_path: Path) -> None:
    page = f"https://www.iesdouyin.com/share/video/{FAKE_AWEME_ID}/"
    api = f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={FAKE_AWEME_ID}"
    client = MapHttp(
        {
            page: html_response(page, EMPTY_HTML),
            api: json_response(api, douyin_iteminfo_json()),
            NWM_VIDEO: bytes_response(NWM_VIDEO, TINY_MP4),
        }
    )
    info = DouyinBackend(client=client).download(page, tmp_path / "item", _options(tmp_path), FakeRunner())
    assert info["id"] == FAKE_AWEME_ID
    assert NWM_VIDEO in client.downloads


def test_douyin_expand_profile_and_unsupported(tmp_path: Path) -> None:
    profile = "https://www.douyin.com/user/MS4wLjABAAAAExample"
    options = _options(tmp_path, expand=True)
    runner = FakeRunner()
    with_list = MapHttp({profile: html_response(profile, douyin_profile_html(with_list=True))})
    items = DouyinBackend(client=with_list).expand(profile, options, runner, max_items=1)
    assert len(items) == 1
    assert PROFILE_VIDEO_A in items[0].url
    empty = MapHttp({profile: html_response(profile, douyin_profile_html(with_list=False))})
    with pytest.raises(JobError, match="暂不支持展开创作者主页"):
        DouyinBackend(client=empty).expand(profile, options, runner, max_items=None)


def test_xhs_image_note_and_video_note(tmp_path: Path) -> None:
    note_url = f"https://www.xiaohongshu.com/explore/{FAKE_NOTE_ID}"
    images_html = xhs_initial_html(video=False, images=True)
    client = MapHttp(
        {
            note_url: html_response(note_url, images_html),
            IMAGE_1: bytes_response(IMAGE_1, TINY_JPG, "image/jpeg"),
            IMAGE_2: bytes_response(IMAGE_2, TINY_JPG, "image/jpeg"),
        }
    )
    dest = tmp_path / "images"
    info = XiaohongshuBackend(client=client).download(note_url, dest, _options(tmp_path), FakeRunner())
    assert info["title"] == "示例笔记标题"
    assert info["uploader"] == "示例笔记作者"
    assert info["media_type"] == "images"
    assert (dest / "media.jpg").is_file()
    assert (dest / "media-01.jpg").read_bytes() == TINY_JPG
    assert (dest / "media-02.jpg").is_file()
    assert info["has_source_subs"] is False

    video_html = xhs_initial_html(video=True, images=False)
    vdest = tmp_path / "video"
    vclient = MapHttp(
        {
            note_url: html_response(note_url, video_html),
            XHS_VIDEO: bytes_response(XHS_VIDEO, TINY_MP4),
        }
    )
    vinfo = XiaohongshuBackend(client=vclient).download(note_url, vdest, _options(tmp_path), FakeRunner())
    assert vinfo["media_type"] == "video"
    assert vinfo["duration"] == pytest.approx(5)
    assert (vdest / "media.mp4").read_bytes() == TINY_MP4


def test_xhs_short_link_and_profile_expand(tmp_path: Path) -> None:
    short = "https://xhslink.com/o/FakeShare"
    note_url = f"https://www.xiaohongshu.com/explore/{FAKE_NOTE_ID}"
    client = MapHttp(
        {
            short: html_response(note_url, xhs_initial_html(images=True)),
            note_url: html_response(note_url, xhs_initial_html(images=True)),
            IMAGE_1: bytes_response(IMAGE_1, TINY_JPG, "image/jpeg"),
            IMAGE_2: bytes_response(IMAGE_2, TINY_JPG, "image/jpeg"),
        }
    )
    info = XiaohongshuBackend(client=client).download(short, tmp_path / "from-short", _options(tmp_path), FakeRunner())
    assert info["id"] == FAKE_NOTE_ID

    profile = "https://www.xiaohongshu.com/user/profile/profileexample"
    listed = XiaohongshuBackend(
        client=MapHttp({profile: html_response(profile, xhs_profile_html(with_list=True))})
    ).expand(profile, _options(tmp_path, expand=True), FakeRunner(), max_items=None)
    assert any(PROFILE_NOTE_A in item.url for item in listed)
    with pytest.raises(JobError, match="暂不支持展开创作者主页"):
        XiaohongshuBackend(
            client=MapHttp({profile: html_response(profile, xhs_profile_html(with_list=False))})
        ).expand(profile, _options(tmp_path, expand=True), FakeRunner(), max_items=None)


def test_fallback_records_both_errors(tmp_path: Path) -> None:
    url = f"https://www.douyin.com/video/{FAKE_AWEME_ID}"
    primary = DouyinBackend(client=MapHttp({url: JobError("公开页解析失败")}))
    runner = FakeRunner(fail_urls={url})
    backend = FallbackBackend(primary, YtDlpBackend(Path("yt-dlp")))
    with pytest.raises(JobError, match="公开页解析失败") as raised:
        backend.download(url, tmp_path / "item", _options(tmp_path), runner)
    message = str(raised.value)
    assert "回退到 yt-dlp 也失败" in message
    assert "video unavailable" in message or "下载失败" in message


def test_fallback_uses_ytdlp_when_dedicated_fails(tmp_path: Path) -> None:
    url = f"https://www.douyin.com/video/{FAKE_AWEME_ID}"
    primary = DouyinBackend(client=MapHttp({url: JobError("专用解析失败")}))
    runner = FakeRunner()
    dest = tmp_path / "item"
    info = FallbackBackend(primary, YtDlpBackend(Path("yt-dlp"))).download(
        url, dest, _options(tmp_path), runner
    )
    assert info["backend"] == "yt-dlp"
    assert info["backend_fallback_from"] == "douyin"
    assert "专用解析失败" in info["primary_error"]
    note = json.loads((dest / "media-intake.json").read_text(encoding="utf-8"))
    assert note["backend_fallback_from"] == "douyin"
    assert (dest / "media.mp4").is_file()


def test_caption_to_srt_from_json() -> None:
    srt = caption_to_srt(CAPTION_PAYLOAD)
    assert srt is not None
    assert "示例字幕第一句" in srt
    assert caption_to_srt({"hello": 1}) is None


def test_xhs_keeps_xsec_token_on_request(tmp_path: Path) -> None:
    note_url = (
        f"https://www.xiaohongshu.com/explore/{FAKE_NOTE_ID}"
        "?xsec_token=demo&xsec_source=app_share"
    )
    client = MapHttp(
        {
            note_url: html_response(note_url, xhs_initial_html(images=True)),
            IMAGE_1: bytes_response(IMAGE_1, TINY_JPG, "image/jpeg"),
            IMAGE_2: bytes_response(IMAGE_2, TINY_JPG, "image/jpeg"),
        }
    )
    XiaohongshuBackend(client=client).download(note_url, tmp_path / "tok", _options(tmp_path), FakeRunner())
    assert note_url in client.calls


def test_bare_xhs_and_cn_domain_match() -> None:
    from media_intake.inputs import _maybe_extract_url

    assert xhs_matches("https://xhslink.cn/o/abcdef")
    extracted = _maybe_extract_url("封面标题 xhslink.com/o/Abc123 ，复制本条后打开小红书")
    assert extracted == "https://xhslink.com/o/Abc123"


def test_douyin_rejects_live(tmp_path: Path) -> None:
    live = "https://webcast.amemv.com/douyin/webcast/reflow/1234567890"
    assert classify_douyin_url(live)[0] == "live"
    assert classify_douyin_url("https://live.douyin.com/123456")[0] == "live"
    short = "https://v.douyin.com/LiveFake/"
    client = MapHttp({short: html_response(live, "<html></html>")})
    with pytest.raises(JobError, match="暂不支持直播"):
        DouyinBackend(client=client).download(short, tmp_path / "live", _options(tmp_path), FakeRunner())


def test_douyin_www_video_uses_share_page(tmp_path: Path) -> None:
    page = f"https://www.douyin.com/video/{FAKE_AWEME_ID}"
    share = f"https://www.iesdouyin.com/share/video/{FAKE_AWEME_ID}/"
    shell = "<html><body></body><script>var glb;glb=_$jsvmprt;</script></html>"
    client = MapHttp(
        {
            page: html_response(page, shell),
            share: html_response(share, douyin_render_html(with_caption=False)),
            NWM_VIDEO: bytes_response(NWM_VIDEO, TINY_MP4),
            PLAY_VIDEO: bytes_response(PLAY_VIDEO, TINY_MP4),
        }
    )
    info = DouyinBackend(client=client).download(page, tmp_path / "from-www", _options(tmp_path), FakeRunner())
    assert info["id"] == FAKE_AWEME_ID
    assert NWM_VIDEO in client.downloads


def test_xhs_http_short_link_retries_https(tmp_path: Path) -> None:
    http_url = "http://xhslink.com/o/FakeShare"
    https_url = "https://xhslink.com/o/FakeShare"
    note_url = f"https://www.xiaohongshu.com/explore/{FAKE_NOTE_ID}"
    client = MapHttp(
        {
            http_url: JobError("HTTP 500：http://xhslink.com/o/FakeShare"),
            https_url: html_response(note_url, xhs_initial_html(images=True)),
            note_url: html_response(note_url, xhs_initial_html(images=True)),
            IMAGE_1: bytes_response(IMAGE_1, TINY_JPG, "image/jpeg"),
            IMAGE_2: bytes_response(IMAGE_2, TINY_JPG, "image/jpeg"),
        }
    )
    info = XiaohongshuBackend(client=client).download(
        http_url, tmp_path / "https-retry", _options(tmp_path), FakeRunner()
    )
    assert info["id"] == FAKE_NOTE_ID
    assert https_url in client.calls


def test_xhs_sec_page_reported(tmp_path: Path) -> None:
    note_url = f"https://www.xiaohongshu.com/explore/{FAKE_NOTE_ID}"
    sec = "https://www.xiaohongshu.com/404/sec_demo?source=xhs_sec_server"
    client = MapHttp({note_url: html_response(sec, EMPTY_HTML)})
    with pytest.raises(JobError, match="安全校验"):
        XiaohongshuBackend(client=client).download(note_url, tmp_path / "sec", _options(tmp_path), FakeRunner())


def test_xhs_retries_original_url_after_sec(tmp_path: Path) -> None:
    from urllib.parse import quote

    short = "https://xhslink.com/o/FakeShare"
    note_http = (
        f"http://www.xiaohongshu.com/discovery/item/{FAKE_NOTE_ID}"
        "?xsec_token=demo&xsec_source=app_share"
    )
    note_https = (
        f"https://www.xiaohongshu.com/discovery/item/{FAKE_NOTE_ID}"
        "?xsec_token=demo&xsec_source=app_share"
    )
    sec = (
        "https://www.xiaohongshu.com/404/sec_demo?source=xhs_sec_server&originalUrl="
        + quote(note_http, safe="")
    )
    client = MapHttp(
        {
            short: html_response(sec, EMPTY_HTML),
            note_http: JobError("HTTP 500"),
            note_https: html_response(note_https, xhs_initial_html(images=True)),
            IMAGE_1: bytes_response(IMAGE_1, TINY_JPG, "image/jpeg"),
            IMAGE_2: bytes_response(IMAGE_2, TINY_JPG, "image/jpeg"),
        }
    )
    info = XiaohongshuBackend(client=client).download(
        short, tmp_path / "orig-retry", _options(tmp_path), FakeRunner()
    )
    assert info["id"] == FAKE_NOTE_ID
    assert note_https in client.calls


def test_xhs_matches_rednote() -> None:
    assert xhs_matches("https://www.rednote.com/discovery/item/aaaaaaaaaaaaaaaaaaaaaaaa")
    assert classify_xhs_url("https://www.rednote.com/discovery/item/aaaaaaaaaaaaaaaaaaaaaaaa")[0] == "note"
    assert classify_xhs_url("https://xhslink.cn/o/abcdef")[0] == "short"
