from __future__ import annotations

import email.message
import http.cookiejar
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPCookieProcessor,
    HTTPSHandler,
    Request,
    build_opener,
)

from media_intake.config import BatchOptions
from media_intake.errors import JobError
from media_intake.inputs import _validate_url

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)


@dataclass
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        ctype = self.headers.get("content-type") or self.headers.get("Content-Type") or ""
        charset = _charset_from_content_type(ctype) or "utf-8"
        return self.body.decode(charset, errors="replace")

    @property
    def content_type(self) -> str:
        value = self.headers.get("content-type") or self.headers.get("Content-Type") or ""
        return value.split(";", 1)[0].strip().lower()


class HttpClient(Protocol):
    def request(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30,
    ) -> HttpResponse:
        """GET 一个 URL，跟随重定向，返回最终地址和正文。"""

    def download(
        self,
        url: str,
        dest: Path,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 120,
    ) -> Path:
        """把响应体写到 dest。"""


@dataclass
class UrllibClient:
    cookiejar: http.cookiejar.CookieJar = field(default_factory=http.cookiejar.CookieJar)
    default_headers: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HEADERS))

    def __post_init__(self) -> None:
        context = ssl.create_default_context()
        self._opener = build_opener(
            HTTPCookieProcessor(self.cookiejar),
            HTTPSHandler(context=context),
        )

    def request(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30,
    ) -> HttpResponse:
        _validate_url(url)
        merged = dict(self.default_headers)
        if headers:
            merged.update(headers)
        req = Request(url, headers=merged, method="GET")
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                body = resp.read()
                raw_headers = {k: v for k, v in resp.headers.items()}
                final = resp.geturl() or url
                status = getattr(resp, "status", None) or resp.getcode() or 200
                if final != url:
                    _validate_url(final)
                return HttpResponse(url=final, status=int(status), headers=raw_headers, body=body)
        except HTTPError as exc:
            body = b""
            try:
                body = exc.read() or b""
            except Exception:
                body = b""
            raise JobError(f"HTTP {exc.code}：{_safe_url(url)}") from exc
        except URLError as exc:
            raise JobError(f"网络请求失败：{_safe_url(url)}（{exc.reason}）") from exc
        except JobError:
            raise
        except Exception as exc:
            raise JobError(f"网络请求失败：{_safe_url(url)}（{type(exc).__name__}）") from exc

    def download(
        self,
        url: str,
        dest: Path,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 120,
    ) -> Path:
        resp = self.request(url, headers=headers, timeout=timeout)
        if resp.status >= 400:
            raise JobError(f"下载媒体失败 HTTP {resp.status}：{_safe_url(url)}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.body)
        if dest.stat().st_size <= 0:
            raise JobError(f"下载媒体为空：{_safe_url(url)}")
        return dest


def parse_browser_spec(spec: str) -> tuple[str, str | None, str | None, str | None]:
    """解析 BROWSER[+KEYRING][:PROFILE][::CONTAINER]，与 yt-dlp 命令行格式一致。"""
    text = spec.strip()
    if not text:
        raise JobError("--cookies-from-browser 不能为空")
    container: str | None = None
    if "::" in text:
        text, container = text.split("::", 1)
        container = container.strip() or None
    profile: str | None = None
    if ":" in text:
        text, profile = text.split(":", 1)
        profile = profile.strip() or None
    keyring: str | None = None
    if "+" in text:
        name, keyring = text.split("+", 1)
        keyring = keyring.strip().upper() or None
    else:
        name = text
    name = name.strip().lower()
    if not name:
        raise JobError("无效的 --cookies-from-browser 参数")
    return name, profile, keyring, container


def cookiejar_from_options(options: BatchOptions) -> http.cookiejar.CookieJar:
    cookie_file = str(options.cookies_file) if options.cookies_file else None
    browser = None
    if options.cookies_from_browser:
        browser = parse_browser_spec(options.cookies_from_browser)
    if not cookie_file and not browser:
        return http.cookiejar.CookieJar()
    try:
        from yt_dlp.cookies import load_cookies

        class _Silent:
            params = {"noprogress": True, "logger": object()}

            def to_stderr(self, message: str) -> None:
                return None

            def report_warning(self, message: str, *args: object, **kwargs: object) -> None:
                return None

            def write_debug(self, message: str, *args: object, **kwargs: object) -> None:
                return None

        return load_cookies(cookie_file, browser, _Silent())
    except Exception as exc:
        raise JobError(f"无法加载 cookies：{type(exc).__name__}") from exc


def client_from_options(options: BatchOptions, client: HttpClient | None = None) -> HttpClient:
    if client is not None:
        return client
    return UrllibClient(cookiejar=cookiejar_from_options(options))


def extension_for(url: str, content_type: str | None = None, default: str = ".bin") -> str:
    path = urlparse(url).path.lower()
    for ext in (
        ".mp4",
        ".webm",
        ".mov",
        ".m4a",
        ".mp3",
        ".m3u8",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".bmp",
        ".srt",
        ".vtt",
        ".json",
    ):
        if path.endswith(ext):
            return ext
    mapping = {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "audio/mp4": ".m4a",
        "audio/mpeg": ".mp3",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "application/json": ".json",
        "text/vtt": ".vtt",
        "application/x-subrip": ".srt",
    }
    if content_type:
        return mapping.get(content_type.split(";", 1)[0].strip().lower(), default)
    return default


def upgrade_http_to_https(url: str) -> str | None:
    """http 短链在部分节点会直接 500，这时改走 https 再试一次。"""
    parsed = urlparse(url)
    if parsed.scheme.lower() != "http":
        return None
    return parsed._replace(scheme="https").geturl()


def mobile_headers(referer: str | None = None, *, ua: str | None = None) -> dict[str, str]:
    headers = {"User-Agent": ua or MOBILE_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    return headers


def request_page(
    client: HttpClient,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30,
) -> HttpResponse:
    """GET 页面；若 http 请求失败，自动再试一次 https。"""
    try:
        return client.request(url, headers=headers, timeout=timeout)
    except JobError:
        alt = upgrade_http_to_https(url)
        if alt:
            return client.request(alt, headers=headers, timeout=timeout)
        raise


def _charset_from_content_type(value: str) -> str | None:
    msg = email.message.Message()
    msg["content-type"] = value
    return msg.get_content_charset()


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return parsed.path or "url"
