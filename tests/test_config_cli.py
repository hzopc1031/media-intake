from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from media_intake.cli import LEGAL_NOTE, main
from media_intake.config import BatchOptions


def test_max_items_requires_expand(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max-items"):
        BatchOptions(library_root=tmp_path, max_items=5)
    with pytest.raises(ValueError, match="大于等于 1"):
        BatchOptions(library_root=tmp_path, expand=True, max_items=0)
    BatchOptions(library_root=tmp_path, expand=True, max_items=5)


def test_concurrency_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="并发"):
        BatchOptions(library_root=tmp_path, concurrency=0)


def test_run_help_lists_legal_note_and_new_flags() -> None:
    buf = io.StringIO()
    with pytest.raises(SystemExit) as exited:
        with redirect_stdout(buf):
            main(["run", "--help"])
    assert exited.value.code == 0
    text = buf.getvalue()
    assert LEGAL_NOTE in text
    assert "--cookies" in text
    assert "--cookies-from-browser" in text
    assert "--expand" in text
    assert "--playlist" in text
    assert "--max-items" in text
    assert "--transcribe" in text
    assert "--whisper-model" in text
    assert "--whisper-device" in text
    assert "--whisper-compute-type" in text
    assert "--whisper-language" in text
    assert "--asr-backend" in text
    assert "--diarize" in text


def test_root_help_contains_legal_note() -> None:
    buf = io.StringIO()
    with pytest.raises(SystemExit) as exited:
        with redirect_stdout(buf):
            main(["--help"])
    assert exited.value.code == 0
    text = buf.getvalue()
    assert LEGAL_NOTE in text
    assert "mcp" in text
    assert "worker" in text
    assert "submit" in text
    assert "cancel" in text


def test_missing_cookies_file_returns_error(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-cookies.txt"
    code = main(
        [
            "run",
            "https://example.com/a",
            "--library",
            str(tmp_path / "lib"),
            "--cookies",
            str(missing),
        ]
    )
    assert code == 2


def test_mcp_help_lists_tools() -> None:
    buf = io.StringIO()
    with pytest.raises(SystemExit) as exited:
        with redirect_stdout(buf):
            main(["mcp", "--help"])
    assert exited.value.code == 0
    text = buf.getvalue()
    assert LEGAL_NOTE in text
    assert "submit_batch" in text
    assert "get_status" in text
    assert "read_manifest" in text
    assert "read_item" in text
    assert "read_transcript" in text
    assert "search_library" in text
    assert "get_metadata" in text
    assert "cancel_batch" in text
    assert "list_batches" in text
    assert "worker" in text
