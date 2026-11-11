from __future__ import annotations

import re
from pathlib import Path

_TS_SRT = re.compile(
    r"^\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}"
)
_TS_VTT = re.compile(
    r"^\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}"
)
_INDEX = re.compile(r"^\d+$")
_TAG = re.compile(r"<[^>]+>")


def subtitle_to_text(content: str, *, suffix: str | None = None) -> str:
    """把 SRT 或 VTT 转为纯文本，去掉序号、时间轴和简单标记。"""
    lines_out: list[str] = []
    seen_blank = True
    for raw in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            if not seen_blank and lines_out:
                lines_out.append("")
            seen_blank = True
            continue
        if line.startswith("WEBVTT") or line.startswith("NOTE ") or line == "NOTE":
            continue
        if line.lower().startswith("kind:") or line.lower().startswith("language:"):
            continue
        if _INDEX.match(line) and seen_blank:
            continue
        if _TS_SRT.match(line) or _TS_VTT.match(line):
            continue
        text = _TAG.sub("", line).strip()
        if text:
            lines_out.append(text)
            seen_blank = False
    # 合并连续空行
    cleaned: list[str] = []
    for line in lines_out:
        if line == "" and (not cleaned or cleaned[-1] == ""):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip() + ("\n" if cleaned else "")


def write_plain_text(subtitle_path: Path, dest: Path) -> Path:
    content = subtitle_path.read_text(encoding="utf-8", errors="replace")
    dest.write_text(
        subtitle_to_text(content, suffix=subtitle_path.suffix),
        encoding="utf-8",
    )
    return dest


def find_sidecar_subtitles(media_path: Path) -> list[Path]:
    """查找与媒体文件同主名的 .srt / .vtt。"""
    parent = media_path.parent
    stem = media_path.stem
    found: list[Path] = []
    for path in sorted(parent.iterdir()) if parent.is_dir() else []:
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".srt", ".vtt"}:
            continue
        name = path.name
        if name.startswith(stem + ".") or path.stem == stem:
            found.append(path)
    return found
