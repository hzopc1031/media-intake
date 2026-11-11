from pathlib import Path

from media_intake.library import Library, manifest_record, render_index, utc_now
from media_intake.models import JobResult, JobStatus, SourceKind
from media_intake.store import JobStore


def _job(job_id: str, status: JobStatus = JobStatus.SUCCEEDED) -> JobResult:
    return JobResult(
        id=job_id,
        source=f"https://example.com/{job_id}",
        source_kind=SourceKind.URL,
        status=status,
        title=f"Title {job_id}",
        duration_seconds=65,
        item_dir=f"items/{job_id}",
        media_path=f"items/{job_id}/media.mp4",
        subtitle_path=f"items/{job_id}/source.srt",
        attempts=1,
        parent_source="https://example.com/playlist",
    )


def test_library_rewrites_manifest_and_index(tmp_path: Path) -> None:
    library = Library(tmp_path / "media-library")
    library.prepare()
    jobs = [_job("aaa"), _job("bbb", JobStatus.FAILED)]
    library.rewrite_outputs(jobs, updated_at="2026-11-11T00:00:00Z")
    lines = library.manifest_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec = manifest_record(jobs[0])
    assert rec["parent_source"] == "https://example.com/playlist"
    assert rec["media_path"] == "items/aaa/media.mp4"
    index = library.index_path.read_text(encoding="utf-8")
    assert "素材索引" in index
    assert "1:05" in index
    assert "失败条目" in index
    assert utc_now().endswith("Z")


def test_store_upsert_and_get(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "state.sqlite")
    job = _job("aaa")
    store.upsert(job, created_at="2026-11-11T00:00:00Z", updated_at="2026-11-11T00:00:01Z")
    loaded = store.get("aaa")
    assert loaded is not None
    assert loaded.title == "Title aaa"
    assert loaded.parent_source == "https://example.com/playlist"
    job.title = "Updated"
    store.upsert(job, created_at="2026-11-11T00:00:02Z", updated_at="2026-11-11T00:00:02Z")
    assert store.get("aaa") is not None
    assert store.get("aaa").title == "Updated"
    assert len(store.list_all()) == 1
    store.close()


def test_rel_paths_are_posix_and_relative(tmp_path: Path) -> None:
    library = Library(tmp_path)
    dest = library.item_dir("abc")
    dest.mkdir(parents=True)
    media = dest / "media.mp4"
    media.write_bytes(b"x")
    assert library.rel(media) == "items/abc/media.mp4"


def test_render_index_escapes_pipes() -> None:
    job = _job("aaa")
    job.title = "A | B"
    text = render_index([job], updated_at="t")
    assert "A \\| B" in text


def test_manifest_and_store_keep_transcript_metadata(tmp_path: Path) -> None:
    job = _job("ccc")
    job.transcript_path = "items/ccc/transcript.srt"
    job.transcript_text_path = "items/ccc/transcript.txt"
    job.transcript_model = "tiny"
    job.transcript_language = "en"
    rec = manifest_record(job)
    assert rec["transcript_model"] == "tiny"
    assert rec["transcript_language"] == "en"
    index = render_index([job], updated_at="t")
    assert "transcript.srt（tiny, en）" in index
    store = JobStore(tmp_path / "state.sqlite")
    store.upsert(job, created_at="2026-11-11T00:00:00Z", updated_at="2026-11-11T00:00:01Z")
    loaded = store.get("ccc")
    assert loaded is not None
    assert loaded.transcript_model == "tiny"
    assert loaded.transcript_language == "en"
    store.close()
