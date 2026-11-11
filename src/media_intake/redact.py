from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

_SECRET_FLAGS = {
    "--cookies",
    "--username",
    "--password",
    "--twofactor",
    "--video-password",
    "--ap-password",
    "-u",
    "-p",
}


def redact_argv(argv: Sequence[str]) -> list[str]:
    """日志里不写出 cookie 文件内容，并遮盖登录相关参数的值。"""
    out: list[str] = []
    hide_next = False
    for token in argv:
        if hide_next:
            out.append("<redacted>")
            hide_next = False
            continue
        if token in _SECRET_FLAGS:
            out.append(token)
            hide_next = True
            continue
        if "=" in token:
            key, _, _value = token.partition("=")
            if key in _SECRET_FLAGS:
                out.append(f"{key}=<redacted>")
                continue
        out.append(token)
    if hide_next:
        out.append("<redacted>")
    return out


_URL_QUERY = re.compile(r"(https?://[^\s?\"'<>]+)(\?[^\s\"'<>]*)")


def redact_error_text(text: str) -> str:
    """去掉错误信息里 URL 的查询串，避免把分享参数写进日志和清单。"""
    return _URL_QUERY.sub(r"\1", text)


def format_redacted_command(argv: Sequence[str]) -> str:
    """用于错误信息：只保留可执行文件名，遮盖密钥参数，URL 显示为 <url>。"""
    tokens = list(argv)
    if tokens:
        tokens[0] = Path(tokens[0]).name
    if tokens:
        tokens = redact_argv(tokens[:-1]) + ["<url>"]
    return " ".join(tokens)
