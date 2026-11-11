"""小红书专用提取器：解析公开笔记页和页面里已提供的视频或图片地址。

不会构造 x-s / x-t 等接口签名。用户提供的 cookies（文件或浏览器）
会随请求发送。创作者主页仅在公开页面已嵌入作品列表时展开。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

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
    upgrade_http_to_https,
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

XIAOHONGSHU_HOSTS = (
    "xiaohongshu.com",
    "xiaohongshu.cn",
    "xhslink.com",
    "xhslink.cn",
    "rednote.com",
)

_NOTE_PATH = re.compile(
    r"/(?:explore|discovery/item|item|apps/item|notes?)/(?P<id>[0-9a-zA-Z]+)",
    re.IGNORECASE,
)
_PROFILE_PATH = re.compile(r"/user/profile/(?P<id>[0-9a-zA-Z]+)", re.IGNORECASE)
_NOTE_URL = "https://www.xiaohongshu.com/explore/{id}"

_IMAGE_KEYS = (
    "original",
    "urlDefault",
    "url_default",
    "urlSizeLarge",
    "url_size_large",
    "url",
    "urlPre",
    "url_pre",
)


def matches(url: str) -> bool:
    extracted = extract_http_url(url) or url
    return host_matches(url_host(extracted), XIAOHONGSHU_HOSTS)


class XiaohongshuBackend:
    name = "xiaohongshu"

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
            note = _note_from_html(html) if html else None
            if note:
                title = metadata_from_note(note).get("title")
            return [ExtractedItem(url=canonical, title=title)]
        items = _items_from_profile(html or "", max_items=max_items)
        if not items:
            raise JobError(
                "小红书专用后端暂不支持展开创作者主页（公开页面里没有可解析的笔记列表）。"
                "请逐条提供笔记链接，或依赖回退到 yt-dlp"
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
                "这是小红书创作者主页，默认只处理单条笔记；"
                "如需展开请使用 --expand（专用后端若无法从公开页得到列表，会再回退到 yt-dlp）"
            )
        note = _note_from_html(html) if html else None
        if note is None:
            raise JobError("未能从小红书公开页面解析出笔记信息（可能被反爬或需要登录后的 cookies）")
        meta = metadata_from_note(note)
        if item_id and not meta.get("id"):
            meta["id"] = item_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        video_urls = select_video_urls(note)
        image_urls = select_image_urls(note)
        if video_urls:
            media_path = _download_video(client, video_urls[0], dest_dir, runner, referer=canonical)
            if options.audio_only:
                media_path = _extract_audio(runner, media_path)
            media_type = "video"
            image_paths: list[str] = []
        elif image_urls:
            if options.audio_only:
                raise JobError("这条小红书笔记是图文，没有可提取的音轨")
            image_paths_abs = _download_images(client, image_urls, dest_dir, referer=canonical)
            media_path = image_paths_abs[0]
            media_type = "images"
            image_paths = [path.name for path in image_paths_abs]
        else:
            raise JobError("小红书页面里没有可用的视频或图片地址")
        caption_written = False
        if options.write_subs and media_type == "video":
            caption_written = _maybe_write_captions(client, note, dest_dir, referer=canonical)
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
            "media_type": media_type,
            "image_paths": image_paths if media_type == "images" else [],
            "has_source_subs": caption_written,
        }
        (dest_dir / "media.info.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return info


def classify_xhs_url(url: str) -> tuple[str, str | None]:
    parsed = urlparse(url)
    path = parsed.path or ""
    note = _NOTE_PATH.search(path)
    if note:
        return "note", note.group("id")
    profile = _PROFILE_PATH.search(path)
    if profile:
        return "profile", profile.group("id")
    host = (parsed.hostname or "").lower()
    if "xhslink.com" in host or "xhslink.cn" in host:
        return "short", None
    return "unknown", None


def metadata_from_note(note: dict) -> dict:
    user = {}
    for key in ("user", "author", "authorInfo", "owner"):
        if isinstance(note.get(key), dict):
            user = note[key]
            break
    title = first_string(note, "title", "name", "displayTitle", "display_title")
    desc = first_string(note, "desc", "description", "content", "text")
    if not title:
        title = desc
    uploader = first_string(user, "nickname", "nick_name", "name", "userName") if user else None
    duration = coerce_duration(
        first_number(note, "duration", "videoDuration")
        or _duration_from_video(note)
    )
    item_id = first_string(note, "noteId", "note_id", "id", "note_oid")
    return {
        "id": item_id,
        "title": title,
        "description": desc or title,
        "uploader": uploader,
        "duration": duration,
    }


def _duration_from_video(note: dict) -> float | None:
    video = note.get("video")
    if not isinstance(video, dict):
        return None
    return first_number(video, "duration") or first_number(
        video.get("media") if isinstance(video.get("media"), dict) else {},
        "duration", "videoDuration",
    )


def find_note(blob: object) -> dict | None:
    best: dict | None = None
    best_score = 0
    for node in iter_dicts(blob):
        score = 0
        if any(k in node for k in ("noteId", "note_id", "imageList", "image_list")):
            score += 2
        if isinstance(node.get("video"), dict) or isinstance(node.get("imageList"), list) or isinstance(
            node.get("image_list"), list
        ):
            score += 3
        if first_string(node, "title", "desc", "description"):
            score += 1
        if score > best_score and score >= 4:
            best, best_score = node, score
    return best


def select_video_urls(note: dict) -> list[str]:
    urls: list[str] = []
    for node in iter_dicts(note):
        for key in ("masterUrl", "master_url", "originVideoKey"):
            # originVideoKey 通常不是可下载 URL，只接受 http
            value = node.get(key)
            if isinstance(value, str) and value.startswith("http"):
                urls.append(value)
        urls.extend(url_list_from(node.get("stream")))
        urls.extend(url_list_from(node.get("media")))
        if "url" in node and isinstance(node.get("url"), str):
            value = node["url"]
            if value.startswith("http") and any(ext in value.lower() for ext in (".mp4", ".m3u8", ".mov")):
                urls.append(value)
    cleaned = []
    for url in unique_keep_order(urls):
        lower = url.lower()
        if any(ext in lower for ext in (".mp4", ".m3u8", ".mov", ".webm")) or "xhscdn" in lower or "videostream" in lower:
            if not any(ext in lower for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
                cleaned.append(url)
    return cleaned


def select_image_urls(note: dict) -> list[str]:
    urls: list[str] = []
    lists: list[list] = []
    for node in iter_dicts(note):
        for key in ("imageList", "image_list", "images", "imageListV2"):
            value = node.get(key)
            if isinstance(value, list) and value:
                lists.append(value)
    for images in lists:
        for item in images:
            picked = _image_url_from(item)
            if picked:
                urls.append(picked)
        if urls:
            break
    if not urls:
        for node in iter_dicts(note):
            picked = _image_url_from(node) if any(k in node for k in _IMAGE_KEYS) else None
            if picked:
                urls.append(picked)
    return unique_keep_order(urls)


def _image_url_from(item: object) -> str | None:
    if isinstance(item, str) and item.startswith("http"):
        return item
    if not isinstance(item, dict):
        return None
    for key in _IMAGE_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://", "//")):
            if value.startswith("//"):
                value = "https:" + value
            return value
        if isinstance(value, dict):
            nested = url_list_from(value)
            if nested:
                return nested[0]
    info_list = item.get("infoList") or item.get("info_list")
    if isinstance(info_list, list):
        for info in info_list:
            found = _image_url_from(info)
            if found:
                return found
    return None


def _resolve(client: HttpClient, raw: str) -> tuple[str, str, str | None, str | None]:
    url = extract_http_url(raw) or raw.strip()
    if not matches(url):
        raise JobError(f"不是小红书链接：{url}")
    kind, item_id = classify_xhs_url(url)
    html = None
    canonical = url
    if kind in {"short", "unknown"} or "xhslink." in url_host(url):
        page = _get_page(client, url)
        canonical = page.url
        html = page.text
        kind, item_id = classify_xhs_url(canonical)
        if kind == "unknown":
            guessed = _note_id_from_html(html)
            if guessed:
                kind, item_id = "note", guessed
    elif kind in {"note", "profile"}:
        # 必须保留原链接上的 xsec_token 等查询参数，不能改写成裸的 /explore/<id>
        page = _get_page(client, url, referer="https://www.xiaohongshu.com/")
        canonical = page.url
        html = page.text
        if kind == "note" and not item_id:
            kind, item_id = classify_xhs_url(canonical)
    if _is_blocked_page(canonical):
        retried = _retry_blocked(client, canonical, html or "")
        if retried is not None:
            canonical, kind, item_id, html = retried
        if _is_blocked_page(canonical) or (kind == "note" and not _note_from_html(html or "")):
            raise JobError(_blocked_message(canonical))
    return canonical, kind, item_id, html


def _get_page(client: HttpClient, url: str, *, referer: str | None = None, ua: str | None = None):
    headers = mobile_headers(referer, ua=ua or MOBILE_UA)
    try:
        page = request_page(client, url, headers=headers, timeout=30)
    except JobError:
        if (ua or MOBILE_UA) != IPHONE_UA:
            return _get_page(client, url, referer=referer, ua=IPHONE_UA)
        raise
    if _is_blocked_page(page.url) and (ua or MOBILE_UA) != IPHONE_UA:
        try:
            alt = _get_page(client, url, referer=referer, ua=IPHONE_UA)
            if not _is_blocked_page(alt.url):
                return alt
        except JobError:
            pass
    return page


def _is_blocked_page(url: str) -> bool:
    path = urlparse(url).path or ""
    return "/404" in path or path.startswith("/login") or "/login" in path


def _blocked_message(url: str) -> str:
    if "sec_" in url or "xhs_sec_server" in url:
        return (
            "小红书返回了安全校验页（反爬拦截）。"
            "可在本机浏览器登录后通过 --cookies 或 --cookies-from-browser 重试；"
            "本工具不会绕过签名或验证码"
        )
    if "/login" in urlparse(url).path:
        return "小红书返回了登录页。可提供 cookies 后重试；本工具不会绕过登录墙"
    return (
        "小红书返回了 404 或登录页。完整分享链接需要保留 xsec_token；"
        "不带令牌的 /explore/<id> 或过期短链常常会落到这种页面"
    )


def _retry_blocked(
    client: HttpClient, url: str, html: str
) -> tuple[str, str, str | None, str] | None:
    """短链落到安全校验页时，尝试页面或跳转参数里已经给出的 originalUrl。"""
    candidates: list[str] = []
    original = _original_url_from(url) or _original_url_from_html(html)
    if original:
        candidates.append(original)
        https = upgrade_http_to_https(original)
        if https:
            candidates.append(https)
        rednote = _to_rednote(original)
        if rednote:
            candidates.append(rednote)
    for cand in _unique(candidates):
        if cand == url or _is_blocked_page(cand):
            continue
        try:
            page = _get_page(client, cand, referer="https://www.xiaohongshu.com/")
        except JobError:
            continue
        if _is_blocked_page(page.url):
            continue
        kind, item_id = classify_xhs_url(page.url)
        if kind == "unknown":
            guessed = _note_id_from_html(page.text)
            if guessed:
                kind, item_id = "note", guessed
        if _note_from_html(page.text) or kind in {"note", "profile"}:
            return page.url, kind, item_id, page.text
    return None


def _original_url_from(url: str) -> str | None:
    query = parse_qs(urlparse(url).query)
    raw = (query.get("originalUrl") or query.get("original_url") or [None])[0]
    if not raw:
        return None
    cand = unquote(raw).strip()
    if cand.startswith("http://") or cand.startswith("https://"):
        return cand
    return None


def _original_url_from_html(html: str) -> str | None:
    match = re.search(r"originalUrl=([^&\"']+)", html)
    if not match:
        return None
    cand = unquote(match.group(1)).strip()
    if cand.startswith("http://") or cand.startswith("https://"):
        return cand
    return None


def _to_rednote(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if "xiaohongshu.com" in host:
        new_host = "www.rednote.com"
        return parsed._replace(netloc=new_host, scheme="https").geturl()
    return None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _note_from_html(html: str) -> dict | None:
    for blob in extract_json_blobs(html):
        found = find_note(blob)
        if found:
            return found
    return None


def _note_id_from_html(html: str) -> str | None:
    match = _NOTE_PATH.search(html)
    if match:
        return match.group("id")
    match = re.search(r'"noteId"\s*:\s*"([0-9a-zA-Z]{6,})"', html)
    if match:
        return match.group(1)
    return None


def _items_from_profile(html: str, *, max_items: int | None) -> list[ExtractedItem]:
    ids: list[str] = []
    seen: set[str] = set()
    for blob in extract_json_blobs(html):
        for node in iter_dicts(blob):
            note_id = first_string(node, "noteId", "note_id")
            if not note_id or note_id in seen:
                continue
            if not (
                isinstance(node.get("video"), dict)
                or isinstance(node.get("imageList"), list)
                or isinstance(node.get("image_list"), list)
                or first_string(node, "title", "desc")
            ):
                continue
            seen.add(note_id)
            ids.append(note_id)
            if max_items is not None and len(ids) >= max_items:
                break
        if max_items is not None and len(ids) >= max_items:
            break
    return [ExtractedItem(url=_NOTE_URL.format(id=item_id)) for item_id in ids]


def _download_video(
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
    ext = extension_for(url, default=".mp4")
    dest = dest_dir / f"media{ext if ext != '.bin' else '.mp4'}"
    client.download(url, dest, headers=headers, timeout=180)
    return dest


def _download_m3u8(runner: CommandRunner, url: str, dest_dir: Path, *, referer: str) -> Path:
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise JobError("该笔记只有 m3u8 播放列表，需要本机 ffmpeg 才能下载")
    dest = dest_dir / "media.mp4"
    argv = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-headers", f"Referer: {referer}\r\n",
        "-i", url, "-c", "copy", "-bsf:a", "aac_adtstoasc", str(dest),
    ]
    result = runner.run(argv, cwd=dest_dir, timeout=180)
    if result.returncode != 0 or not dest.is_file():
        raise JobError("ffmpeg 未能把小红书 m3u8 写成文件")
    return dest


def _extract_audio(runner: CommandRunner, media_path: Path) -> Path:
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise JobError("只提取音频需要本机 ffmpeg")
    dest = media_path.with_name("media.m4a")
    argv = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(media_path), "-vn", "-c:a", "aac", str(dest),
    ]
    result = runner.run(argv, timeout=120)
    if result.returncode != 0 or not dest.is_file():
        raise JobError("ffmpeg 提取音频失败")
    if media_path.resolve() != dest.resolve() and media_path.exists():
        media_path.unlink()
    return dest


def _download_images(client: HttpClient, urls: list[str], dest_dir: Path, *, referer: str) -> list[Path]:
    headers = {**mobile_headers(referer), "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
    saved: list[Path] = []
    for index, url in enumerate(urls, start=1):
        resp_ext = extension_for(url, default=".jpg")
        if resp_ext in {".bin", ".json"}:
            resp_ext = ".jpg"
        path = dest_dir / f"media-{index:02d}{resp_ext}"
        client.download(url, path, headers=headers, timeout=60)
        saved.append(path)
    if not saved:
        raise JobError("没有成功下载任何小红书图片")
    # 固定主文件名，便于素材库识别
    primary = dest_dir / f"media{saved[0].suffix}"
    if primary.resolve() != saved[0].resolve():
        primary.write_bytes(saved[0].read_bytes())
        saved.insert(0, primary)
    return saved


def _maybe_write_captions(client: HttpClient, note: dict, dest_dir: Path, *, referer: str) -> bool:
    urls: list[str] = []
    for node in iter_dicts(note):
        for key in ("caption", "subtitle", "subtitles", "captionUrl", "subtitle_url"):
            value = node.get(key)
            if isinstance(value, str) and value.startswith("http") and any(
                ext in value.lower() for ext in (".srt", ".vtt", ".json")
            ):
                urls.append(value)
            if isinstance(value, dict):
                urls.extend(u for u in url_list_from(value) if u.startswith("http"))
    for url in unique_keep_order(urls):
        try:
            resp = client.request(url, headers={"Referer": referer, "Accept": "*/*"}, timeout=30)
        except JobError:
            continue
        text = resp.text
        if "-->" in text:
            (dest_dir / "source.und.srt").write_text(
                text if text.endswith("\n") else text + "\n", encoding="utf-8"
            )
            return True
    return False
