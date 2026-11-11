"""检查 Netscape cookies 文件或浏览器 cookies 是否覆盖抖音 / 小红书，且不打印 cookie 值。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from media_intake.config import BatchOptions
from media_intake.errors import InputError, JobError

_DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")
_XHS_DOMAINS = ("xiaohongshu.com", "xhslink.com", "xhslink.cn", "rednote.com")

# 登录态相关；只比较名称，绝不读取或输出值。
_DOUYIN_LOGIN = ("sessionid", "sessionid_ss", "sid_tt", "sid_guard")
_DOUYIN_USEFUL = ("ttwid", "odin_tt", "passport_csrf_token")
_XHS_LOGIN = ("web_session",)
_XHS_USEFUL = ("a1", "webid")


@dataclass(frozen=True)
class CookieMeta:
    domain: str
    name: str
    expires: int | None
    httponly: bool = False


@dataclass
class PlatformCookieStatus:
    platform: str
    status: str
    detail: str
    names_found: list[str] = field(default_factory=list)
    domains_found: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    missing_login: list[str] = field(default_factory=list)
    missing_useful: list[str] = field(default_factory=list)


def parse_netscape_cookies(path: Path) -> list[CookieMeta]:
    """解析 Netscape cookies 文件，只保留域名、名称和过期时间。"""
    if not path.is_file():
        raise InputError(f"找不到 cookies 文件：{path}")
    cookies: list[CookieMeta] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        httponly = False
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
            httponly = True
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            parts = line.split()
        if len(parts) < 7:
            continue
        domain, _flag, _path, _secure, expiry_s, name, _value = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
        if not name:
            continue
        expires: int | None
        try:
            expires = int(float(expiry_s))
        except (TypeError, ValueError):
            expires = None
        if expires == 0:
            expires = None
        cookies.append(
            CookieMeta(domain=domain.lstrip("."), name=name, expires=expires, httponly=httponly)
        )
    return cookies


def load_browser_cookies(spec: str) -> list[CookieMeta]:
    """从本机浏览器读取 cookie 的名称、域名和过期时间，不保留值。"""
    options = BatchOptions(library_root=Path("."), cookies_from_browser=spec)
    try:
        from media_intake.extractors.httputil import cookiejar_from_options

        jar = cookiejar_from_options(options)
    except JobError as exc:
        raise InputError(f"无法从浏览器读取 cookies：{exc}") from exc
    except Exception as exc:
        raise InputError(f"无法从浏览器读取 cookies：{type(exc).__name__}") from exc
    cookies: list[CookieMeta] = []
    for item in jar:
        domain = str(getattr(item, "domain", "") or "").lstrip(".")
        name = str(getattr(item, "name", "") or "")
        if not name:
            continue
        expires = getattr(item, "expires", None)
        try:
            expires_i = int(expires) if expires else None
        except (TypeError, ValueError):
            expires_i = None
        cookies.append(CookieMeta(domain=domain, name=name, expires=expires_i))
    return cookies


def inspect_cookies(
    *,
    cookies_file: Path | None = None,
    cookies_from_browser: str | None = None,
) -> tuple[str, list[PlatformCookieStatus], int]:
    if cookies_file is None and not cookies_from_browser:
        raise InputError("请提供 --cookies 文件，或 --cookies-from-browser")
    source_parts: list[str] = []
    collected: list[CookieMeta] = []
    if cookies_file is not None:
        path = Path(cookies_file).expanduser()
        collected.extend(parse_netscape_cookies(path))
        source_parts.append(f"文件 {path}")
    if cookies_from_browser:
        collected.extend(load_browser_cookies(cookies_from_browser))
        source_parts.append(f"浏览器 {cookies_from_browser}")
    reports = [
        _platform_status("抖音", collected, _DOUYIN_DOMAINS, _DOUYIN_LOGIN, _DOUYIN_USEFUL),
        _platform_status("小红书", collected, _XHS_DOMAINS, _XHS_LOGIN, _XHS_USEFUL),
    ]
    text = format_cookie_report(source_parts, reports, total=len(collected))
    code = 0 if all(item.status == "ok" for item in reports) else 1
    return text, reports, code


def format_cookie_report(
    sources: Iterable[str],
    reports: list[PlatformCookieStatus],
    *,
    total: int,
) -> str:
    lines = [
        "Cookie 自检：",
        "  来源：" + "；".join(sources),
        f"  读取到 {total} 条 cookie 记录（只统计名称、域名和过期时间，不会打印值）。",
        "",
    ]
    for item in reports:
        mark = {"ok": "ok", "partial": "部分", "expired": "过期", "missing": "缺少"}.get(item.status, item.status)
        lines.append(f"  [{mark}] {item.platform}：{item.detail}")
        if item.names_found:
            lines.append("      已找到：" + "、".join(item.names_found))
        if item.expired:
            lines.append("      已过期：" + "、".join(item.expired))
        if item.missing_login:
            lines.append("      缺少登录相关：" + "、".join(item.missing_login))
        if item.missing_useful and item.status != "ok":
            lines.append("      建议同时具备：" + "、".join(item.missing_useful))
        lines.append(f"      {_guidance(item)}")
        lines.append("")
    lines.append("本工具不会绕过登录墙、安全校验或签名参数。若专用后端或 yt-dlp 仍失败，")
    lines.append("可用你信任的平台专用工具取到文件后，再执行 media-intake import 入库。")
    lines.append("")
    return "\n".join(lines)


def _platform_status(
    platform: str,
    cookies: list[CookieMeta],
    domains: tuple[str, ...],
    login_names: tuple[str, ...],
    useful_names: tuple[str, ...],
) -> PlatformCookieStatus:
    now = int(time.time())
    matched = [c for c in cookies if _domain_matches(c.domain, domains)]
    names = sorted({c.name for c in matched}, key=str.lower)
    domains_found = sorted({c.domain for c in matched}, key=str.lower)
    if not matched:
        return PlatformCookieStatus(
            platform=platform,
            status="missing",
            detail="没有找到该站点域名下的 cookie。",
            missing_login=list(login_names),
            missing_useful=list(useful_names),
        )
    login_present: list[CookieMeta] = []
    expired: list[str] = []
    for cookie in matched:
        if cookie.name.lower() not in {n.lower() for n in login_names}:
            continue
        if cookie.expires is not None and cookie.expires < now:
            expired.append(f"{cookie.name}（{_format_expiry(cookie.expires)}）")
        else:
            login_present.append(cookie)
    useful_found = {c.name.lower() for c in matched if c.name.lower() in {n.lower() for n in useful_names}}
    missing_useful = [name for name in useful_names if name.lower() not in useful_found]
    missing_login = [name for name in login_names if name.lower() not in {c.name.lower() for c in matched}]
    if expired and not login_present:
        status = "expired"
        detail = "找到登录相关 cookie，但均已过期。"
    elif not login_present:
        status = "partial"
        detail = "找到该站点 cookie，但缺少登录相关字段。"
    elif expired:
        status = "partial"
        detail = "部分登录相关 cookie 仍然有效，另有已过期项。"
    else:
        status = "ok"
        detail = "找到尚未过期的登录相关 cookie，看起来可用；不能保证站点一定放行。"
    return PlatformCookieStatus(
        platform=platform,
        status=status,
        detail=detail,
        names_found=names,
        domains_found=domains_found,
        expired=expired,
        missing_login=missing_login,
        missing_useful=missing_useful,
    )


def _domain_matches(domain: str, suffixes: tuple[str, ...]) -> bool:
    host = domain.lower().lstrip(".")
    for suffix in suffixes:
        target = suffix.lower().lstrip(".")
        if host == target or host.endswith("." + target):
            return True
    return False


def _format_expiry(expires: int) -> str:
    return datetime.fromtimestamp(expires, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _guidance(item: PlatformCookieStatus) -> str:
    if item.status == "ok":
        return "若下载仍失败，多半是页面反爬或安全校验，而不是 cookie 缺失。"
    if item.status == "expired":
        return "请重新登录对应网站，再导出 cookies 或改用 --cookies-from-browser。"
    return (
        "请在本机浏览器登录后，使用 --cookies 提供 Netscape 格式文件，"
        "或使用 --cookies-from-browser。不要把 cookie 文件提交到 git。"
    )
