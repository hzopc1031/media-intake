"""读取可选的 media-intake.toml 配置。命令行参数仍然覆盖配置文件。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from media_intake.errors import InputError

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover - 仅在极简环境
        tomllib = None  # type: ignore[assignment]

# 配置文件允许的键。不要把令牌写进配置；cookies 路径如需使用，请留在本机文件中且不要提交。
KNOWN_KEYS = {
    "library",
    "concurrency",
    "retries",
    "retry_wait",
    "format",
    "audio_only",
    "subs",
    "sub_langs",
    "cookies",
    "cookies_from_browser",
    "expand",
    "max_items",
    "transcribe",
    "asr_backend",
    "diarize",
    "whisper_model",
    "whisper_device",
    "whisper_compute_type",
    "whisper_language",
}


def default_config_paths(explicit: str | Path | None = None) -> list[Path]:
    paths: list[Path] = []
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    paths.append(xdg / "media-intake" / "config.toml")
    paths.append(Path.cwd() / "media-intake.toml")
    if explicit:
        paths.append(Path(explicit).expanduser())
    return paths


def load_settings(explicit: str | Path | None = None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    last_path: Path | None = None
    for path in default_config_paths(explicit):
        if not path.is_file():
            if explicit and path == Path(explicit).expanduser() and not path.is_file():
                raise InputError(f"找不到配置文件：{path}")
            continue
        data = _read_toml(path)
        last_path = path
        for key, value in data.items():
            if key in KNOWN_KEYS:
                merged[key] = value
    if last_path is not None:
        merged["_config_path"] = str(last_path)
    return merged


def _read_toml(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if tomllib is None:
        return _parse_flat_toml(raw.decode("utf-8"))
    data = tomllib.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise InputError(f"配置文件不是有效的表：{path}")
    # 允许可选的 [default] 段
    if "default" in data and isinstance(data["default"], dict):
        merged = dict(data["default"])
        merged.update({k: v for k, v in data.items() if k != "default"})
        data = merged
    return {str(k): v for k, v in data.items() if not isinstance(v, dict)}


def _parse_flat_toml(text: str) -> dict[str, Any]:
    """极简回退解析：只支持顶层 key = 标量。"""
    result: dict[str, Any] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        result[key] = _parse_scalar(value.strip())
    return result


def _parse_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
