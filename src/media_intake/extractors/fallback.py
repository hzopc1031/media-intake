from __future__ import annotations

import json
from pathlib import Path

from media_intake.config import BatchOptions
from media_intake.errors import IntakeError, JobError
from media_intake.extractors.base import ExtractedItem, MediaBackend
from media_intake.process import CommandRunner


class FallbackBackend:
    """站点专用后端失败时回退到另一个后端（通常是 yt-dlp），并记录两侧错误。"""

    def __init__(self, primary: MediaBackend, fallback: MediaBackend) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name

    def matches(self, url: str) -> bool:
        return self.primary.matches(url)

    def expand(
        self,
        url: str,
        options: BatchOptions,
        runner: CommandRunner,
        *,
        max_items: int | None,
    ) -> list[ExtractedItem]:
        try:
            return self.primary.expand(url, options, runner, max_items=max_items)
        except Exception as primary_exc:
            try:
                return self.fallback.expand(url, options, runner, max_items=max_items)
            except Exception as fallback_exc:
                raise JobError(_combined_error("展开", self.primary, primary_exc, self.fallback, fallback_exc)) from fallback_exc

    def download(
        self,
        url: str,
        dest_dir: Path,
        options: BatchOptions,
        runner: CommandRunner,
    ) -> dict:
        try:
            info = self.primary.download(url, dest_dir, options, runner)
            info.setdefault("backend", self.primary.name)
            return info
        except Exception as primary_exc:
            try:
                info = dict(self.fallback.download(url, dest_dir, options, runner))
            except Exception as fallback_exc:
                raise JobError(
                    _combined_error("下载", self.primary, primary_exc, self.fallback, fallback_exc)
                ) from fallback_exc
            info["backend"] = self.fallback.name
            info["backend_fallback_from"] = self.primary.name
            info["primary_error"] = _error_text(primary_exc)
            _write_fallback_note(dest_dir, info)
            return info


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, IntakeError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _combined_error(
    action: str,
    primary: MediaBackend,
    primary_exc: BaseException,
    fallback: MediaBackend,
    fallback_exc: BaseException,
) -> str:
    return (
        f"{primary.name} {action}失败：{_error_text(primary_exc)}；"
        f"回退到 {fallback.name} 也失败：{_error_text(fallback_exc)}"
    )


def _write_fallback_note(dest_dir: Path, info: dict) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "backend": info.get("backend"),
        "backend_fallback_from": info.get("backend_fallback_from"),
        "primary_error": info.get("primary_error"),
    }
    (dest_dir / "media-intake.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
