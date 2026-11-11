"""合成的抖音 / 小红书面具，不含真实用户数据或 cookies。"""

from __future__ import annotations

import json
from urllib.parse import quote

FAKE_AWEME_ID = "7000000000000000001"
FAKE_NOTE_ID = "aaaaaaaaaaaaaaaaaaaaaaaa"
NWM_VIDEO = "https://media.example.com/nwm.mp4"
PLAY_VIDEO = "https://media.example.com/play.mp4"
CAPTION_JSON = "https://media.example.com/caption.json"
IMAGE_1 = "https://media.example.com/note-1.jpg"
IMAGE_2 = "https://media.example.com/note-2.jpg"
XHS_VIDEO = "https://media.example.com/note.mp4"
PROFILE_VIDEO_A = "7000000000000000002"
PROFILE_VIDEO_B = "7000000000000000003"
PROFILE_NOTE_A = "bbbbbbbbbbbbbbbbbbbbbbbb"
PROFILE_NOTE_B = "cccccccccccccccccccccccc"

TINY_MP4 = b"fake-mp4-bytes-for-tests"
TINY_JPG = b"\xff\xd8\xfffake-jpeg-for-tests\xff\xd9"
CAPTION_PAYLOAD = {
    "utterances": [
        {"start_time": 0, "end_time": 1200, "text": "示例字幕第一句"},
        {"start_time": 1200, "end_time": 2400, "text": "示例字幕第二句"},
    ]
}


def douyin_aweme(*, with_caption: bool = True, with_download: bool = True) -> dict:
    video = {
        "duration": 3200,
        "play_addr": {"url_list": [PLAY_VIDEO]},
        "bit_rate": [{"width": 720, "play_addr": {"url_list": [PLAY_VIDEO]}}],
    }
    if with_download:
        video["download_addr"] = {"url_list": [NWM_VIDEO]}
    aweme = {
        "aweme_id": FAKE_AWEME_ID,
        "desc": "示例视频描述",
        "author": {"nickname": "示例作者", "unique_id": "example_user"},
        "video": video,
    }
    if with_caption:
        aweme["cla_info"] = {
            "caption_infos": [
                {"language": "zh", "url": CAPTION_JSON, "caption_format": "json"}
            ]
        }
    return aweme


def douyin_render_html(*, with_caption: bool = True, with_download: bool = True) -> str:
    payload = {"app": {"aweme": {"aweme_detail": douyin_aweme(with_caption=with_caption, with_download=with_download)}}}
    encoded = quote(json.dumps(payload, ensure_ascii=False), safe="")
    return (
        "<!doctype html><html><head><title>示例视频</title></head><body>"
        f'<script id="RENDER_DATA" type="application/json">{encoded}</script>'
        "</body></html>"
    )


def douyin_iteminfo_json() -> str:
    return json.dumps({"status_code": 0, "item_list": [douyin_aweme()]}, ensure_ascii=False)


def douyin_profile_html(*, with_list: bool = True) -> str:
    if with_list:
        state = {
            "user": {
                "aweme_list": [
                    {
                        "aweme_id": PROFILE_VIDEO_A,
                        "desc": "主页作品甲",
                        "video": {"play_addr": {"url_list": [PLAY_VIDEO]}},
                    },
                    {
                        "aweme_id": PROFILE_VIDEO_B,
                        "desc": "主页作品乙",
                        "video": {"play_addr": {"url_list": [PLAY_VIDEO]}},
                    },
                ]
            }
        }
    else:
        state = {"user": {"uid": "MS4wLjABAAAAExampleProfile", "nickname": "示例作者"}}
    body = json.dumps(state, ensure_ascii=False)
    return (
        "<!doctype html><html><body>"
        f"<script>window.__INITIAL_STATE__={body};</script>"
        "</body></html>"
    )


def xhs_note(video: bool = False, images: bool = True) -> dict:
    note = {
        "noteId": FAKE_NOTE_ID,
        "title": "示例笔记标题",
        "desc": "示例笔记正文",
        "user": {"nickname": "示例笔记作者"},
    }
    if video:
        note["type"] = "video"
        note["duration"] = 5
        note["video"] = {
            "media": {
                "stream": {
                    "h264": [{"masterUrl": XHS_VIDEO, "width": 720}]
                }
            }
        }
    if images:
        note["imageList"] = [
            {"urlDefault": IMAGE_1, "url": IMAGE_1},
            {"urlDefault": IMAGE_2, "url": IMAGE_2},
        ]
    return note


def xhs_initial_html(*, video: bool = False, images: bool = True) -> str:
    state = {"note": {"noteDetailMap": {FAKE_NOTE_ID: {"note": xhs_note(video=video, images=images)}}}}
    body = json.dumps(state, ensure_ascii=False)
    return (
        "<!doctype html><html><head><title>示例笔记</title></head><body>"
        f"<script>window.__INITIAL_STATE__={body};</script>"
        "</body></html>"
    )


def xhs_profile_html(*, with_list: bool = True) -> str:
    if with_list:
        state = {
            "user": {
                "notes": [
                    {"noteId": PROFILE_NOTE_A, "title": "笔记甲", "imageList": [{"url": IMAGE_1}]},
                    {"noteId": PROFILE_NOTE_B, "title": "笔记乙", "imageList": [{"url": IMAGE_2}]},
                ]
            }
        }
    else:
        state = {"user": {"userId": "profileexample", "nickname": "示例笔记作者"}}
    body = json.dumps(state, ensure_ascii=False)
    return (
        "<!doctype html><html><body>"
        f"<script>window.__INITIAL_STATE__={body};</script>"
        "</body></html>"
    )


EMPTY_HTML = "<!doctype html><html><body><p>no data</p></body></html>"
