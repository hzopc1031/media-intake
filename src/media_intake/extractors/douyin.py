"""抖音专用提取器：解析公开分享页和页面里已提供的媒体地址。

只使用页面或公开接口已经给出的地址。不会改写播放地址去绕水印，
也不会生成 X-Bogus / a_bogus 等签名参数。加载用户提供的 cookies
（文件或浏览器）后，由 HTTP 客户端按常规方式发送。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from media_intake.config import BatchOptions
from media_intake.errors import JobError
from media_intake.extractors.base import ExtractedItem, host_matches, url_host
from media_intake.extractors.httputil import (
    HttpClient,
    IPHONE_UA,
    MOBILE_UA,
    client_from_options,
    extension_for,
    mobile_headers,
    request_page,
)
from media_intake.extractors.parseutil import (
    coerce_duration,
    extract_http_url,
    extract_json_blobs,
    first_number,
    first_string,
    iter_dicts,
    unique_keep_order,
    url_list_from,
)
from media_intake.process import CommandRunner

DOUYIN_HOSTS = (
    "douyin.com",
    "iesdouyin.com",
    "iesdouyin.net",
    "amemv.com",
)

_VIDEO_PATH = re.compile(r"/(?:share/)?(?:video|note|aweme)/(?P<id>\d+)", re.IGNORECASE)
_USER_PATH = re.compile(r"/user/(?P<id>[\w-]+)", re.IGNORECASE)
_ITEMINFO = "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={id}"
_SHARE_PAGE = "https://www.iesdouyin.com/share/video/{id}/"


def matches(url: str) -> bool:
    extracted = extract_http_url(url) or url
    return host_matches(url_host(extracted), DOUYIN_HOSTS)


class DouyinBackend:
    name = "douyin"

    def __init__(self, client: HttpClient | None = None) -> None:
        self._client = client

    def matches(self, url: str) -> bool:
        return matches(url)

    def expand(
        self,
        url: str,
        options: BatchOptions,
        runner: CommandRunner,
        *,
        max_items: int | None,
    ) -> list[ExtractedItem]:
        client = client_from_options(options, self._client)
        canonical, kind, item_id, html = _resolve(client, url)
        if kind != "profile":
            title = None
            duration = None
            aweme = _aweme_from_html(html) if html else None
            if aweme:
                meta = metadata_from_aweme(aweme)
                title = meta.get("title")
                duration = meta.get("duration")
            return [
                ExtractedItem(
                    url=canonical,
                    title=title,
                    duration_seconds=duration,
                )
            ]
        items = _items_from_profile(html or "", max_items=max_items)
        if not items:
            raise JobError(
                "抖音专用后端暂不支持展开创作者主页（公开页面里没有可解析的作品列表）。"
                "请逐条提供视频链接，或依赖回退到 yt-dlp"
            )
        return items

    def download(
        self,
        url: str,
        dest_dir: Path,
        options: BatchOptions,
        runner: CommandRunner,
    ) -> dict:
        client = client_from_options(options, self._client)
        canonical, kind, item_id, html = _resolve(client, url)
        if kind == "profile":
            raise JobError(
                "这是抖音创作者主页，默认只处理单条视频；"
                "如需展开请使用 --expand（专用后端若无法从公开页得到列表，会再回退到 yt-dlp）"
            )
        aweme = _aweme_from_html(html) if html else None
        if aweme is None and item_id:
            aweme = _aweme_from_iteminfo(client, item_id)
        if aweme is None:
            raise JobError(
                "未能从抖音公开页面解析出视频信息（桌面页常为脚本壳；"
                "未签名的 iteminfo 接口也可能返回 encrypt_data_miss。"
                "可提供 cookies 后重试；本工具不会生成签名）"
            )
        meta = metadata_from_aweme(aweme)
        if item_id and not meta.get("id"):
            meta["id"] = item_id
        streams = select_video_urls(aweme)
        if not streams:
            raise JobError("抖音页面里没有可用的视频地址")
        dest_dir.mkdir(parents=True, exist_ok=True)
        chosen = streams[0]
        media_path = _download_stream(client, chosen.url, dest_dir, runner, referer=canonical)
        if options.audio_only:
            media_path = _extract_audio(runner, media_path)
        caption_written = False
        if options.write_subs:
            caption_written = _maybe_write_captions(
                client, aweme, dest_dir, preferred_langs=_lang_list(options.sub_langs)
            )
        info = {
            "id": meta.get("id"),
            "title": meta.get("title"),
            "description": meta.get("description"),
            "uploader": meta.get("uploader"),
            "duration": meta.get("duration"),
            "webpage_url": canonical,
            "extractor": self.name,
            "backend": self.name,
            "ext": media_path.suffix.lstrip("."),
            "watermark_free": chosen.watermark_free,
            "media_type": "video",
            "has_source_subs": caption_written,
        }
        (dest_dir / "media.info.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return info


def classify_douyin_url(url: str) -> tuple[str, str | None]:
    parsed = urlparse(url)
    if _is_live_url(url):
        return "live", None
    query = parse_qs(parsed.query)
    modal = (query.get("modal_id") or query.get("aweme_id") or query.get("item_ids") or [None])[0]
    if modal and str(modal).isdigit():
        return "video", str(modal)
    found = _VIDEO_PATH.search(parsed.path or "")
    if found:
        return "video", found.group("id")
    user = _USER_PATH.search(parsed.path or "")
    if user:
        return "profile", user.group("id")
    host = (parsed.hostname or "").lower()
    if host.startswith("v.") or host == "v.douyin.com":
        return "short", None
    return "unknown", None


def _is_live_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if "webcast" in host or "amemv.com" in host or host.startswith("live."):
        return True
    if "/webcast/" in path or re.search(r"/live(?:/|$)", path):
        return True
    return False


def _live_unsupported() -> JobError:
    return JobError("抖音专用后端暂不支持直播间链接。请提供已发布的单条视频分享链接，或依赖回退到 yt-dlp")


def metadata_from_aweme(aweme: dict) -> dict:
    author = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
    video = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
    title = first_string(aweme, "desc", "title", "preview_title", "previewTitle", "share_title")
    uploader = first_string(author, "nickname", "nick_name", "unique_id", "uid") if author else None
    duration = coerce_duration(
        first_number(video, "duration") or first_number(aweme, "duration")
    )
    item_id = first_string(aweme, "aweme_id", "awemeId", "group_id", "groupId", "id")
    if item_id is None:
        raw_id = aweme.get("aweme_id") or aweme.get("id")
        if raw_id is not None:
            item_id = str(raw_id)
    return {
        "id": item_id,
        "title": title,
        "description": title,
        "uploader": uploader,
        "duration": duration,
    }


class _Stream:
    def __init__(self, url: str, watermark_free: bool, width: float | None = None) -> None:
        self.url = url
        self.watermark_free = watermark_free
        self.width = width or 0


def select_video_urls(aweme: dict) -> list[_Stream]:
    """按页面已提供的字段挑选视频地址：优先 download_addr，不改写 URL。"""
    streams: list[_Stream] = []

    def _add(addr: object, watermark_free: bool, width: float | None = None) -> None:
        for url in unique_keep_order(url_list_from(addr)):
            if url.startswith(("http://", "https://")):
                streams.append(_Stream(url, watermark_free, width))

    for node in iter_dicts(aweme):
        width = first_number(node, "width")
        if "download_addr" in node or "downloadAddr" in node:
            _add(node.get("download_addr") or node.get("downloadAddr"), True, width)
        if "play_addr" in node or "playAddr" in node:
            _add(node.get("play_addr") or node.get("playAddr"), False, width)
    # 有无水印候选时，只保留它们并按宽度排序；否则用播放地址
    preferred = [s for s in streams if s.watermark_free]
    pool = preferred or streams
    pool.sort(key=lambda s: s.width, reverse=True)
    return unique_streams(pool)


def unique_streams(streams: list[_Stream]) -> list[_Stream]:
    seen: set[str] = set()
    out: list[_Stream] = []
    for stream in streams:
        if stream.url in seen:
            continue
        seen.add(stream.url)
        out.append(stream)
    return out


def caption_entries(aweme: dict) -> list[dict]:
    found: list[dict] = []
    for node in iter_dicts(aweme):
        if "caption_infos" in node and isinstance(node["caption_infos"], list):
            for item in node["caption_infos"]:
                if isinstance(item, dict):
                    found.append(item)
        if first_string(node, "caption", "caption_url", "url") and (
            "caption" in node or "caption_url" in node or node.get("caption_format")
        ):
            found.append(node)
    return found


def caption_to_srt(payload: object) -> str | None:
    if isinstance(payload, str):
        text = payload.strip()
        if "-->" in text:
            return text if text.endswith("\n") else text + "\n"
        return None
    if not isinstance(payload, dict):
        return None
    utterances = (
        payload.get("utterances")
        or payload.get("sentences")
        or payload.get("captions")
        or payload.get("word_list")
    )
    if not isinstance(utterances, list) or not utterances:
        return None
    lines: list[str] = []
    index = 1
    for item in utterances:
        if not isinstance(item, dict):
            continue
        text = first_string(item, "text", "content", "caption")
        if not text:
            continue
        start = first_number(item, "start_time", "start", "begin_time", "begin") or 0
        end = first_number(item, "end_time", "end", "stop_time") or (start + 1000)
        # 字段里常见毫秒
        if start >= 1000 or end >= 1000:
            start_s, end_s = start / 1000.0, end / 1000.0
        else:
            start_s, end_s = start, end
        if end_s <= start_s:
            end_s = start_s + 0.5
        lines.append(str(index))
        lines.append(f"{_ts(start_s)} --> {_ts(end_s)}")
        lines.append(text)
        lines.append("")
        index += 1
    return "\n".join(lines) + ("\n" if lines else "") if lines else None


def _ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _resolve(client: HttpClient, raw: str) -> tuple[str, str, str | None, str | None]:
    url = extract_http_url(raw) or raw.strip()
    if not matches(url):
        raise JobError(f"不是抖音链接：{url}")
    kind, item_id = classify_douyin_url(url)
    if kind == "live":
        raise _live_unsupported()
    html = None
    canonical = url
    if kind in {"short", "unknown"} or (kind == "video" and "v.douyin.com" in url_host(url)):
        page = _get_page(client, url)
        canonical = page.url
        html = page.text
        kind, item_id = classify_douyin_url(canonical)
        if kind == "live":
            raise _live_unsupported()
        if kind == "unknown" and item_id is None:
            guessed = _video_id_from_html(html)
            if guessed:
                kind, item_id = "video", guessed
        if kind == "video" and item_id and not _aweme_from_html(html or ""):
            html2, url2 = _try_share_page(client, item_id)
            if html2:
                html = html2
                canonical = url2 or canonical
    elif kind == "video" and item_id:
        html2, url2 = _try_share_page(client, item_id)
        if html2:
            html = html2
            canonical = url2 or _SHARE_PAGE.format(id=item_id)
        else:
            page = _get_page(client, url)
            canonical = page.url
            html = page.text
        if not _aweme_from_html(html or "") and url2 != url:
            try:
                page = _get_page(client, url)
                if _aweme_from_html(page.text):
                    canonical = page.url
                    html = page.text
            except JobError:
                pass
    elif kind == "profile":
        page = _get_page(client, url)
        canonical = page.url
        html = page.text
    return canonical, kind, item_id, html


def _try_share_page(client: HttpClient, item_id: str) -> tuple[str | None, str | None]:
    share = _SHARE_PAGE.format(id=item_id)
    try:
        page = _get_page(client, share)
    except JobError:
        return None, None
    return page.text, page.url or share


def _get_page(client: HttpClient, url: str, *, ua: str | None = None):
    """公开分享页在桌面 UA 下常被重定向到 www.douyin.com 的脚本壳，优先用移动 UA。"""
    headers = mobile_headers(ua=ua or MOBILE_UA)
    try:
        page = request_page(client, url, headers=headers, timeout=30)
    except JobError:
        if (ua or MOBILE_UA) != IPHONE_UA:
            return _get_page(client, url, ua=IPHONE_UA)
        raise
    if _looks_like_desktop_shell(page.text) and (ua or MOBILE_UA) != IPHONE_UA:
        try:
            return _get_page(client, url, ua=IPHONE_UA)
        except JobError:
            return page
    return page


def _looks_like_desktop_shell(html: str) -> bool:
    if "_$jsvmprt" in html:
        return True
    if "RENDER_DATA" in html or "_ROUTER_DATA" in html or "aweme_id" in html or "awemeId" in html:
        return False
    return False


def _aweme_from_html(html: str) -> dict | None:
    for blob in extract_json_blobs(html):
        found = find_aweme(blob)
        if found:
            return found
    return None


def find_aweme(blob: object) -> dict | None:
    best: dict | None = None
    best_score = 0
    for node in iter_dicts(blob):
        score = 0
        if any(k in node for k in ("aweme_id", "awemeId", "group_id", "groupId")):
            score += 2
        video = node.get("video")
        if isinstance(video, dict):
            score += 2
            if any(k in video for k in ("play_addr", "playAddr", "download_addr", "downloadAddr", "bit_rate")):
                score += 3
        if any(k in node for k in ("play_addr", "download_addr", "playAddr", "downloadAddr")):
            score += 2
        if "desc" in node or "title" in node:
            score += 1
        if score > best_score and score >= 4:
            best, best_score = node, score
    return best


def _aweme_from_iteminfo(client: HttpClient, item_id: str) -> dict | None:
    url = _ITEMINFO.format(id=item_id)
    try:
        resp = client.request(
            url,
            headers={
                **mobile_headers(referer=_SHARE_PAGE.format(id=item_id)),
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=30,
        )
    except JobError:
        return None
    try:
        data = json.loads(resp.text)
    except json.JSONDecodeError:
        return None
    items = data.get("item_list") or data.get("aweme_list") or []
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    if isinstance(data, dict):
        return find_aweme(data)
    return None


def _video_id_from_html(html: str) -> str | None:
    match = _VIDEO_PATH.search(html)
    if match:
        return match.group("id")
    match = re.search(r'"aweme_id"\s*:\s*"?(\d{6,})', html)
    if match:
        return match.group(1)
    return None


def _items_from_profile(html: str, *, max_items: int | None) -> list[ExtractedItem]:
    ids: list[str] = []
    seen: set[str] = set()
    for blob in extract_json_blobs(html):
        for node in iter_dicts(blob):
            aweme_id = first_string(node, "aweme_id", "awemeId") or (
                str(node.get("aweme_id")) if isinstance(node.get("aweme_id"), int) else None
            )
            if not aweme_id or not aweme_id.isdigit() or aweme_id in seen:
                continue
            if not isinstance(node.get("video"), dict) and "desc" not in node:
                continue
            seen.add(aweme_id)
            ids.append(aweme_id)
            if max_items is not None and len(ids) >= max_items:
                break
        if max_items is not None and len(ids) >= max_items:
            break
    items = [
        ExtractedItem(url=f"https://www.douyin.com/video/{item_id}")
        for item_id in ids
    ]
    return items


def _download_stream(
    client: HttpClient,
    url: str,
    dest_dir: Path,
    runner: CommandRunner,
    *,
    referer: str,
) -> Path:
    headers = {**mobile_headers(referer), "Accept": "*/*"}
    if ".m3u8" in url.lower():
        return _download_m3u8(runner, url, dest_dir, referer=referer)
    tmp_ext = extension_for(url, default=".mp4")
    if tmp_ext == ".m3u8":
        return _download_m3u8(runner, url, dest_dir, referer=referer)
    dest = dest_dir / f"media{tmp_ext if tmp_ext != '.bin' else '.mp4'}"
    client.download(url, dest, headers=headers, timeout=180)
    return dest


def _download_m3u8(runner: CommandRunner, url: str, dest_dir: Path, *, referer: str) -> Path:
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise JobError("该视频只有 m3u8 播放列表，需要本机 ffmpeg 才能下载")
    dest = dest_dir / "media.mp4"
    argv = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-headers",
        f"Referer: {referer}\r\n",
        "-i",
        url,
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        str(dest),
    ]
    result = runner.run(argv, cwd=dest_dir, timeout=180)
    if result.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 0:
        # 复制失败时再尝试重编码
        argv = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-headers",
            f"Referer: {referer}\r\n",
            "-i",
            url,
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(dest),
        ]
        result = runner.run(argv, cwd=dest_dir, timeout=180)
        if result.returncode != 0 or not dest.is_file():
            raise JobError("ffmpeg 未能把抖音 m3u8 写成文件")
    return dest


def _extract_audio(runner: CommandRunner, media_path: Path) -> Path:
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise JobError("只提取音频需要本机 ffmpeg")
    dest = media_path.with_name("media.m4a")
    argv = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(media_path),
        "-vn",
        "-c:a",
        "aac",
        str(dest),
    ]
    result = runner.run(argv, timeout=120)
    if result.returncode != 0 or not dest.is_file():
        raise JobError("ffmpeg 提取音频失败")
    if media_path.resolve() != dest.resolve() and media_path.exists():
        media_path.unlink()
    return dest


def _maybe_write_captions(
    client: HttpClient,
    aweme: dict,
    dest_dir: Path,
    *,
    preferred_langs: list[str],
) -> bool:
    entries = caption_entries(aweme)
    if not entries:
        return False
    chosen = _choose_caption(entries, preferred_langs)
    url = first_string(chosen, "url", "caption_url", "uri", "caption")
    if not url or not url.startswith("http"):
        # 有的 caption 字段是文本而不是 URL
        srt = caption_to_srt(chosen)
        if srt:
            (dest_dir / "source.zh.srt").write_text(srt, encoding="utf-8")
            return True
        return False
    try:
        resp = client.request(url, headers={"Referer": "https://www.douyin.com/", "Accept": "*/*"}, timeout=30)
    except JobError:
        return False
    text = resp.text
    srt = None
    if "-->" in text:
        srt = text if text.endswith("\n") else text + "\n"
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = text
        srt = caption_to_srt(payload)
    if not srt:
        return False
    lang = first_string(chosen, "language", "lang", "code") or "und"
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "", lang) or "und"
    (dest_dir / f"source.{safe}.srt").write_text(srt, encoding="utf-8")
    return True


def _choose_caption(entries: list[dict], preferred_langs: list[str]) -> dict:
    if preferred_langs:
        lowered = [lang.lower() for lang in preferred_langs]
        for lang in lowered:
            for item in entries:
                value = (first_string(item, "language", "lang", "code") or "").lower()
                if value == lang or value.startswith(lang):
                    return item
    return entries[0]


def _lang_list(spec: str | None) -> list[str]:
    if not spec:
        return []
    return [part.strip() for part in spec.split(",") if part.strip()]
