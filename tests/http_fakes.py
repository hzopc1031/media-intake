from __future__ import annotations

from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from media_intake.errors import JobError
from media_intake.extractors.httputil import HttpResponse


class MapHttp:
    """按 URL 路径或完整地址返回预先录制的响应，不访问网络。"""

    def __init__(self, routes: dict[str, HttpResponse | Callable[..., HttpResponse] | Exception]) -> None:
        self.routes = routes
        self.calls: list[str] = []
        self.downloads: list[str] = []

    def request(self, url: str, *, headers=None, timeout: float = 30) -> HttpResponse:
        self.calls.append(url)
        resp = self._lookup(url)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def download(self, url: str, dest: Path, *, headers=None, timeout: float = 120) -> Path:
        self.downloads.append(url)
        resp = self.request(url, headers=headers, timeout=timeout)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.body)
        return dest

    def _lookup(self, url: str) -> HttpResponse | Exception | Callable:
        if url in self.routes:
            return _eval(self.routes[url], url)
        parsed = urlparse(url)
        key = parsed.path
        if key in self.routes:
            return _eval(self.routes[key], url)
        # 前缀匹配，长的优先
        matches = [k for k in self.routes if url.startswith(k) or key.startswith(k)]
        if matches:
            matches.sort(key=len, reverse=True)
            return _eval(self.routes[matches[0]], url)
        raise JobError(f"夹具中没有这个 URL：{url}")


def _eval(value, url: str):
    if callable(value) and not isinstance(value, Exception):
        return value(url)
    return value


def html_response(url: str, body: str, status: int = 200) -> HttpResponse:
    return HttpResponse(
        url=url,
        status=status,
        headers={"content-type": "text/html; charset=utf-8"},
        body=body.encode("utf-8"),
    )


def bytes_response(url: str, body: bytes, content_type: str = "video/mp4") -> HttpResponse:
    return HttpResponse(
        url=url,
        status=200,
        headers={"content-type": content_type},
        body=body,
    )


def json_response(url: str, body: str) -> HttpResponse:
    return HttpResponse(
        url=url,
        status=200,
        headers={"content-type": "application/json; charset=utf-8"},
        body=body.encode("utf-8"),
    )
