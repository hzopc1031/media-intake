"""从素材库读取条目元数据和字幕/转写正文，供 MCP 与命令行共用。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from media_intake.errors import InputError
from media_intake.library import MANIFEST_NAME

DEFAULT_PAGE_LIMIT = 50_000
MAX_PAGE_LIMIT = 100_000
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 200


def resolve_library(library: str | Path) -> Path:
    if library is None or not str(library).strip():
        raise InputError("请提供素材库目录 library")
    return Path(library).expanduser().resolve()


def load_manifest_records(library: str | Path) -> list[dict[str, Any]]:
    root = resolve_library(library)
    path = root / MANIFEST_NAME
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def find_record(library: str | Path, item_id: str) -> dict[str, Any]:
    item_id = (item_id or "").strip()
    if not item_id:
        raise InputError("请提供条目 ID")
    for rec in load_manifest_records(library):
        if rec.get("id") == item_id:
            return rec
    raise InputError(f"清单中找不到条目：{item_id}")


def _library_file(root: Path, rel: str | None) -> Path | None:
    if not rel:
        return None
    path = Path(rel)
    if not path.is_absolute():
        path = root / path
    return path


def _read_text_file(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def read_item(library: str | Path, item_id: str) -> dict[str, Any]:
    """返回条目元数据，并标明正文是否可读（不把整篇正文塞进结果）。"""
    root = resolve_library(library)
    rec = find_record(root, item_id)
    source_path = _library_file(root, rec.get("subtitle_text_path") or rec.get("subtitle_path"))
    transcript_path = _library_file(root, rec.get("transcript_text_path") or rec.get("transcript_path"))
    source_text = _read_text_file(source_path)
    transcript_text = _read_text_file(transcript_path)
    words_path = _library_file(root, rec.get("transcript_words_path"))
    return {
        "kind": "item",
        "library": str(root),
        "id": rec.get("id"),
        "source": rec.get("source"),
        "source_kind": rec.get("source_kind"),
        "title": rec.get("title"),
        "duration_seconds": rec.get("duration_seconds"),
        "status": rec.get("status"),
        "error": rec.get("error"),
        "item_dir": rec.get("item_dir"),
        "media_path": rec.get("media_path"),
        "subtitle_path": rec.get("subtitle_path"),
        "subtitle_text_path": rec.get("subtitle_text_path"),
        "subtitle_kind": rec.get("subtitle_kind"),
        "transcript_path": rec.get("transcript_path"),
        "transcript_text_path": rec.get("transcript_text_path"),
        "transcript_words_path": rec.get("transcript_words_path"),
        "transcript_model": rec.get("transcript_model"),
        "transcript_language": rec.get("transcript_language"),
        "transcript_engine": rec.get("transcript_engine"),
        "transcript_diarized": rec.get("transcript_diarized"),
        "parent_source": rec.get("parent_source"),
        "platform": rec.get("platform"),
        "uploader": rec.get("uploader"),
        "upload_date": rec.get("upload_date"),
        "thumbnail_path": rec.get("thumbnail_path"),
        "has_source_text": source_text is not None and bool(source_text.strip()),
        "has_transcript": transcript_text is not None and bool(transcript_text.strip()),
        "has_words": words_path is not None and words_path.is_file(),
        "source_text_length": len(source_text) if source_text is not None else 0,
        "transcript_text_length": len(transcript_text) if transcript_text is not None else 0,
    }


def read_transcript(
    library: str | Path,
    item_id: str,
    *,
    kind: str = "auto",
    offset: int = 0,
    limit: int | None = DEFAULT_PAGE_LIMIT,
) -> dict[str, Any]:
    """把来源字幕或转写纯文本直接返回给调用方，按字符分页。"""
    root = resolve_library(library)
    rec = find_record(root, item_id)
    text, used = _select_text(root, rec, kind)
    if text is None:
        available = []
        if rec.get("transcript_text_path") or rec.get("transcript_path"):
            available.append("transcript")
        if rec.get("subtitle_text_path") or rec.get("subtitle_path"):
            available.append("source")
        hint = "、".join(available) if available else "无"
        raise InputError(f"条目 {item_id} 没有可读取的正文（可用：{hint}）")
    return _paginate(
        text,
        offset=offset,
        limit=limit,
        extra={
            "kind": "transcript",
            "library": str(root),
            "id": rec.get("id"),
            "title": rec.get("title"),
            "text_kind": used,
            "language": rec.get("transcript_language") if used == "transcript" else None,
            "model": rec.get("transcript_model") if used == "transcript" else None,
            "engine": rec.get("transcript_engine") if used == "transcript" else None,
        },
    )


def search_library(
    library: str | Path,
    query: str,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    in_text: bool = True,
) -> dict[str, Any]:
    """按标题、来源、ID 或正文片段检索素材库。"""
    root = resolve_library(library)
    needle = (query or "").strip()
    if not needle:
        raise InputError("请提供检索词 query")
    if limit < 1:
        raise InputError("limit 必须大于等于 1")
    if limit > MAX_SEARCH_LIMIT:
        limit = MAX_SEARCH_LIMIT
    lowered = needle.lower()
    matches: list[dict[str, Any]] = []
    for rec in load_manifest_records(root):
        fields = {
            "id": str(rec.get("id") or ""),
            "title": str(rec.get("title") or ""),
            "source": str(rec.get("source") or ""),
            "uploader": str(rec.get("uploader") or ""),
            "platform": str(rec.get("platform") or ""),
        }
        hit_in: list[str] = [name for name, value in fields.items() if lowered in value.lower()]
        snippet = None
        if not hit_in and in_text:
            snippet_source, snippet = _search_text_snippet(root, rec, lowered)
            if snippet is not None:
                hit_in.append(snippet_source)
        if not hit_in:
            continue
        matches.append(
            {
                "id": rec.get("id"),
                "title": rec.get("title"),
                "source": rec.get("source"),
                "source_kind": rec.get("source_kind"),
                "status": rec.get("status"),
                "matched_in": hit_in,
                "snippet": snippet,
            }
        )
        if len(matches) >= limit:
            break
    return {
        "library": str(root),
        "query": needle,
        "count": len(matches),
        "records": matches,
    }


def _select_text(root: Path, rec: dict[str, Any], kind: str) -> tuple[str | None, str]:
    choice = (kind or "auto").strip().lower()
    if choice not in {"auto", "transcript", "source"}:
        raise InputError("kind 必须是 auto、transcript 或 source")
    transcript = _read_text_file(
        _library_file(root, rec.get("transcript_text_path") or rec.get("transcript_path"))
    )
    source = _read_text_file(
        _library_file(root, rec.get("subtitle_text_path") or rec.get("subtitle_path"))
    )
    if choice == "transcript":
        return transcript, "transcript"
    if choice == "source":
        return source, "source"
    if transcript is not None and transcript.strip():
        return transcript, "transcript"
    if source is not None and source.strip():
        return source, "source"
    return None, "auto"


def _paginate(
    text: str,
    *,
    offset: int,
    limit: int | None,
    extra: dict[str, Any],
) -> dict[str, Any]:
    if offset < 0:
        raise InputError("offset 不能为负数")
    if limit is None:
        page_limit = DEFAULT_PAGE_LIMIT
    else:
        if limit < 1:
            raise InputError("limit 必须大于等于 1")
        page_limit = min(limit, MAX_PAGE_LIMIT)
    total = len(text)
    chunk = text[offset : offset + page_limit] if offset < total else ""
    next_offset = offset + len(chunk) if offset + len(chunk) < total else None
    result = {
        **extra,
        "text": chunk,
        "offset": offset,
        "limit": page_limit,
        "returned_length": len(chunk),
        "total_length": total,
        "next_offset": next_offset,
    }
    return result


def _search_text_snippet(
    root: Path, rec: dict[str, Any], lowered: str, *, radius: int = 60
) -> tuple[str, str | None]:
    for label, key in (
        ("transcript", rec.get("transcript_text_path") or rec.get("transcript_path")),
        ("source", rec.get("subtitle_text_path") or rec.get("subtitle_path")),
    ):
        text = _read_text_file(_library_file(root, key))
        if not text:
            continue
        found_at = text.lower().find(lowered)
        if found_at < 0:
            continue
        start = max(0, found_at - radius)
        end = min(len(text), found_at + len(lowered) + radius)
        snippet = text[start:end].replace("\n", " ").strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet = snippet + "…"
        return label, snippet
    return "text", None
