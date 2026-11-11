from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from media_intake.errors import InputError
from media_intake.models import Source, SourceKind

_TRAILING_PUNCT = ')。,，]、}\'"'
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_BARE_SHARE = re.compile(
    r'(?<![\w./])((?:v\.douyin\.com|www\.douyin\.com|www\.iesdouyin\.com|'
    r'xhslink\.(?:com|cn)|www\.xiaohongshu\.com|www\.rednote\.com)/[^\s<>]+)',
    re.IGNORECASE,
)

_TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "si",
    "feature",
    "pp",
    "start_radio",
}


def parse_lines(lines: Iterable[str]) -> list[str]:
    """从文本行取出条目：忽略空行和 # 注释。"""
    items: list[str] = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        items.append(text)
    return items


def read_input_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return parse_lines(text.splitlines())


def classify(raw: str, *, cwd: Path | None = None) -> Source:
    text = raw.strip()
    if not text:
        raise InputError("空条目")
    base = cwd or Path.cwd()

    extracted = _maybe_extract_url(text)
    if extracted:
        _validate_url(extracted)
        identity = normalize_url(extracted)
        return Source(raw=extracted, kind=SourceKind.URL, identity=identity)

    if text.startswith("file://"):
        raise InputError("不接受 file:// 链接，请直接给出本地文件路径")

    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    else:
        path = path.resolve()
    return Source(raw=text, kind=SourceKind.LOCAL, identity=str(path))


def source_id(source: Source) -> str:
    return hashlib.sha256(source.identity.encode("utf-8")).hexdigest()[:12]


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_QUERY_KEYS
    ]
    # 保留相对顺序，去掉追踪参数即可；不去掉 list=，以便同一条目身份稳定。
    cleaned = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        fragment="",
        query=urlencode(query, doseq=True),
    )
    return urlunparse(cleaned)


def _maybe_extract_url(text: str) -> str | None:
    if text.startswith(("http://", "https://")):
        return text.split()[0]
    match = _URL_IN_TEXT.search(text)
    if match:
        return match.group(0).rstrip(_TRAILING_PUNCT)
    bare = _BARE_SHARE.search(text)
    if bare:
        return "https://" + bare.group(1).rstrip(_TRAILING_PUNCT)
    return None


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise InputError(f"只接受 http/https 链接或本地文件：{url}")
    host = parsed.hostname
    if not host:
        raise InputError(f"链接缺少主机名：{url}")
    if host.lower() in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        raise InputError(f"拒绝访问本机地址：{url}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or not ip.is_global:
        raise InputError(f"拒绝访问非公网地址：{url}")
