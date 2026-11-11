from __future__ import annotations

from collections.abc import Sequence

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
