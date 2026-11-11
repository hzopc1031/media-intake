from __future__ import annotations

import json
from pathlib import Path

from media_intake.cli import main
from tests.conftest import generate_sample_media, requires_ffmpeg, write_sample_srt


@requires_ffmpeg
def test_cli_archives_local_media_writes_manifest_and_skips_on_rerun(tmp_path: Path) -> None:
    media = tmp_path / "talk.mp4"
    generate_sample_media(media, duration=1.0)
    write_sample_srt(tmp_path / "talk.srt", "这是本地字幕。")
    library = tmp_path / "media-library"

    code = main(["run", str(media), "--library", str(library), "--retries", "0"])
    assert code == 0
    manifest = library / "manifest.jsonl"
    index = library / "index.md"
    assert manifest.is_file()
    assert index.is_file()
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line]
    assert len(records) == 1
    rec = records[0]
    assert rec["status"] == "succeeded"
    assert rec["source_kind"] == "local"
    assert rec["title"]
    assert rec["duration_seconds"] is not None
    assert rec["duration_seconds"] > 0
    assert rec["media_path"].endswith(".mp4")
    assert rec["subtitle_path"] == f"items/{rec['id']}/source.srt"
    assert rec["subtitle_text_path"] == f"items/{rec['id']}/source.txt"
    media_file = library / rec["media_path"]
    assert media_file.is_file() and media_file.stat().st_size > 0
    text = (library / rec["subtitle_text_path"]).read_text(encoding="utf-8")
    assert "这是本地字幕" in text
    assert "素材索引" in index.read_text(encoding="utf-8")
    assert rec["id"] in index.read_text(encoding="utf-8")

    # 再跑一次应跳过
    code2 = main(["run", str(media), "--library", str(library), "--retries", "0"])
    assert code2 == 0
    records2 = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line]
    assert len(records2) == 1


@requires_ffmpeg
def test_cli_batch_file_and_missing_item_nonzero_exit(tmp_path: Path) -> None:
    media = tmp_path / "ok.mp4"
    generate_sample_media(media, duration=1.0)
    missing = tmp_path / "no-such.mp4"
    batch = tmp_path / "batch.txt"
    batch.write_text(
        f"# 注释\n\n{media}\n{missing}\n",
        encoding="utf-8",
    )
    library = tmp_path / "lib"
    code = main(
        [
            "run",
            "--input",
            str(batch),
            "--library",
            str(library),
            "--retries",
            "0",
            "--concurrency",
            "1",
        ]
    )
    assert code == 1
    records = [json.loads(line) for line in (library / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line]
    statuses = {row["status"] for row in records}
    assert "succeeded" in statuses
    assert "failed" in statuses
    errors = " ".join(row.get("error") or "" for row in records)
    assert "不存在" in errors


@requires_ffmpeg
def test_cli_stdin_and_doctor(tmp_path: Path, monkeypatch) -> None:
    import io
    import sys

    media = tmp_path / "clip.mp4"
    generate_sample_media(media, duration=1.0)
    library = tmp_path / "lib"
    monkeypatch.setattr(sys, "stdin", io.StringIO(str(media) + "\n"))
    code = main(["run", "--input", "-", "--library", str(library), "--retries", "0"])
    assert code == 0
    assert (library / "manifest.jsonl").is_file()

    doctor_code = main(["doctor"])
    assert doctor_code in {0, 1}
