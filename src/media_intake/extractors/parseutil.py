from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable, Iterator
from typing import Any
from urllib.parse import unquote, urlparse

from media_intake.inputs import _maybe_extract_url

_SCRIPT_ID = re.compile(
    r'<script[^>]*\bid=["\'](?P<id>RENDER_DATA|__RENDER_DATA__|'
    r'__UNIVERSAL_DATA_FOR_REHYDRATION__|__NEXT_DATA__|'
    r'_ROUTER_DATA|SERIALIZED_STATE)["\'][^>]*>(?P<body>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_JSON_TYPE = re.compile(
    r'<script[^>]*type=["\']application/json["\'][^>]*>(?P<body>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_WINDOW_NAMES = (
    "__INITIAL_STATE__",
    "_RENDER_DATA",
    "__RENDER_DATA__",
    "_ROUTER_DATA",
    "__INITIAL_SSR_STATE__",
)


def extract_http_url(text: str) -> str | None:
    return _maybe_extract_url(text.strip())


def extract_json_blobs(html_text: str) -> list[Any]:
    """从页面里取出嵌入的 JSON（RENDER_DATA、__INITIAL_STATE__ 等）。"""
    blobs: list[Any] = []
    seen: set[int] = set()

    def _add(raw: str) -> None:
        parsed = try_parse_embedded_json(raw)
        if parsed is None:
            return
        marker = id(parsed) if not isinstance(parsed, (dict, list)) else hash(_finger(parsed))
        if marker in seen:
            return
        seen.add(marker)
        blobs.append(parsed)

    for pat in (_SCRIPT_ID, _SCRIPT_JSON_TYPE):
        for match in pat.finditer(html_text):
            _add(match.group("body"))
    for snippet in iter_window_objects(html_text, _WINDOW_NAMES):
        _add(snippet)
    return blobs


def iter_window_objects(html_text: str, names: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for name in names:
        needle = f"window.{name}"
        start = 0
        while True:
            pos = html_text.find(needle, start)
            if pos < 0:
                break
            eq = html_text.find("=", pos + len(needle))
            if eq < 0:
                break
            cursor = eq + 1
            while cursor < len(html_text) and html_text[cursor].isspace():
                cursor += 1
            if cursor >= len(html_text) or html_text[cursor] != "{":
                start = cursor if cursor > pos else pos + 1
                continue
            end = _matching_brace(html_text, cursor)
            if end is not None:
                found.append(html_text[cursor : end + 1])
                start = end + 1
            else:
                start = cursor + 1
    return found


def _matching_brace(text: str, start: int) -> int | None:
    depth = 0
    in_str: str | None = None
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_str:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_str:
                in_str = None
            continue
        if char in {'"', "'"}:
            in_str = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def try_parse_embedded_json(raw: str) -> Any | None:
    text = html.unescape(raw.strip())
    if not text:
        return None
    candidates = [text]
    if text.startswith("%") or "%7B" in text[:8].upper() or "%22" in text[:12]:
        try:
            candidates.insert(0, unquote(text))
        except Exception:
            pass
    for item in candidates:
        cleaned = _prepare_js_object(item)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            continue
    return None


def _prepare_js_object(text: str) -> str:
    cleaned = text.strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    # 页面里常见的 JS 字面量，转成 JSON
    cleaned = re.sub(r"\bundefined\b", "null", cleaned)
    return cleaned


def _finger(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)[:2000]
    except TypeError:
        return str(type(value))


def iter_dicts(obj: Any) -> Iterator[dict]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_dicts(item)


def first_string(data: dict, *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip() and value.strip().lower() not in {"undefined", "null"}:
            return value.strip()
    return None


def first_number(data: dict, *keys: str) -> float | None:
    for key in keys:
        value = data.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def coerce_duration(value: float | None) -> float | None:
    if value is None:
        return None
    if value >= 1000:
        return value / 1000.0
    return value


def url_list_from(addr: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(addr, str) and addr.startswith(("http://", "https://", "//")):
        urls.append(_abs_http(addr))
    elif isinstance(addr, list):
        for item in addr:
            urls.extend(url_list_from(item))
    elif isinstance(addr, dict):
        for key in ("url_list", "urlList", "urls", "backupUrls", "backup_urls"):
            if key in addr:
                urls.extend(url_list_from(addr[key]))
        for key in ("url", "uri", "masterUrl", "master_url", "download_url", "play_url"):
            if key in addr:
                urls.extend(url_list_from(addr[key]))
    return [u for u in urls if u and u not in (None,)]


def _abs_http(url: str) -> str:
    text = url.strip().rstrip("\\").rstrip()
    if text.startswith("//"):
        return "https:" + text
    return text


def looks_like_media_url(url: str) -> bool:
    lowered = url.lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    if any(
        ext in lowered
        for ext in (
            ".mp4",
            ".m3u8",
            ".webm",
            ".mov",
            ".m4a",
            ".mp3",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
        )
    ):
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(
        key in host
        for key in (
            "vod",
            "cdn",
            "byte",
            "douyinvod",
            "xhscdn",
            "sns-webpic",
            "ci.xiaohongshu",
            "xiaohongshu.com",
        )
    )


def unique_keep_order(urls: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out
