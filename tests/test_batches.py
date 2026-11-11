from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from media_intake.batches import BatchManager, new_batch_id
from media_intake.errors import DependencyError, InputError
from media_intake.library import Library, MANIFEST_NAME
from media_intake.models import JobResult, JobStatus, RunReport, SourceKind
from media_intake.store import JobStore


def _wait(manager: BatchManager, batch_id: str, library: Path, *, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        last = manager.get_status(batch_id, str(library))
        if last["status"] in {"succeeded", "completed", "failed"}:
            return last
        time.sleep(0.02)
    raise AssertionError(f"批次未在时限内结束：{last}")


def _fake_run(jobs: list[JobResult] | None = None, *, fail: Exception | None = None):
    def run(items: list[str], options, **kwargs) -> RunReport:
        if fail is not None:
            raise fail
        from media_intake.library import utc_now

        library = Library(options.library_root)
        library.prepare()
        store = JobStore(library.state_path)
        try:
            results = list(jobs) if jobs is not None else []
            if jobs is None:
                for index, item in enumerate(items, start=1):
                    job = JobResult(
                        id=f"{index:012x}",
                        source=item,
                        source_kind=SourceKind.LOCAL,
                        status=JobStatus.SUCCEEDED,
                        title=f"item-{index}",
                    )
                    results.append(job)
            stamp = utc_now()
            for job in results:
                store.upsert(job, created_at=stamp, updated_at=stamp)
            library.rewrite_outputs(store.list_all())
            succeeded = [j for j in results if j.status is JobStatus.SUCCEEDED]
            skipped = [j for j in results if j.status is JobStatus.SKIPPED]
            failed = [j for j in results if j.status is JobStatus.FAILED]
            return RunReport(succeeded=succeeded, skipped=skipped, failed=failed)
        finally:
            store.close()

    return run


def test_new_batch_id_does_not_look_like_job_id() -> None:
    batch_id = new_batch_id()
    assert batch_id.startswith("batch-")
    assert len(batch_id) > 12


def test_submit_batch_rejects_empty_items(tmp_path: Path) -> None:
    manager = BatchManager(run_batch_fn=_fake_run())
    with pytest.raises(InputError, match="不能为空"):
        manager.submit_batch([], str(tmp_path / "lib"))
    with pytest.raises(InputError, match="不能为空"):
        manager.submit_batch(["  ", ""], str(tmp_path / "lib"))


def test_submit_batch_rejects_missing_cookies_file(tmp_path: Path) -> None:
    manager = BatchManager(run_batch_fn=_fake_run())
    missing = tmp_path / "no-such-cookies.txt"
    with pytest.raises(InputError, match="cookies"):
        manager.submit_batch(["clip.mp4"], str(tmp_path / "lib"), cookies=str(missing))


def test_submit_batch_rejects_max_items_without_expand(tmp_path: Path) -> None:
    manager = BatchManager(run_batch_fn=_fake_run())
    with pytest.raises(InputError, match="max-items"):
        manager.submit_batch(["clip.mp4"], str(tmp_path / "lib"), max_items=3)


def test_submit_and_get_status_for_batch_and_item(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    job = JobResult(
        id="aabbccddeeff",
        source="clip.mp4",
        source_kind=SourceKind.LOCAL,
        status=JobStatus.SUCCEEDED,
        title="clip",
        error=None,
    )
    manager = BatchManager(run_batch_fn=_fake_run([job]))
    submitted = manager.submit_batch(["clip.mp4"], str(library))
    assert submitted["batch_id"].startswith("batch-")
    assert submitted["item_count"] == 1
    assert submitted["status"] in {"queued", "running", "succeeded"}
    status = _wait(manager, submitted["batch_id"], library)
    assert status["kind"] == "batch"
    assert status["status"] == "succeeded"
    assert status["counts"]["succeeded"] == 1
    assert status["counts"]["failed"] == 0
    assert status["jobs"][0]["id"] == "aabbccddeeff"
    assert status["jobs"][0]["status"] == "succeeded"
    item = manager.get_status("aabbccddeeff", str(library))
    assert item["kind"] == "item"
    assert item["id"] == "aabbccddeeff"
    assert item["status"] == "succeeded"
    assert item["title"] == "clip"


def test_get_status_unknown_item_needs_library(tmp_path: Path) -> None:
    manager = BatchManager(run_batch_fn=_fake_run())
    with pytest.raises(InputError, match="library"):
        manager.get_status("aabbccddeeff")
    with pytest.raises(InputError, match="找不到"):
        manager.get_status("aabbccddeeff", str(tmp_path / "empty-lib"))


def test_worker_records_background_error(tmp_path: Path) -> None:
    manager = BatchManager(run_batch_fn=_fake_run(fail=RuntimeError("boom")))
    submitted = manager.submit_batch(["clip.mp4"], str(tmp_path / "lib"))
    status = _wait(manager, submitted["batch_id"], tmp_path / "lib")
    assert status["status"] == "failed"
    assert status["error"] is not None
    assert "boom" in status["error"]
    assert "RuntimeError" in status["error"]


def test_get_status_reloads_batch_from_disk(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    first = BatchManager(run_batch_fn=_fake_run())
    submitted = first.submit_batch(["clip.mp4"], str(library))
    _wait(first, submitted["batch_id"], library)
    second = BatchManager(run_batch_fn=_fake_run())
    status = second.get_status(submitted["batch_id"], str(library))
    assert status["kind"] == "batch"
    assert status["status"] == "succeeded"
    assert status["counts"]["succeeded"] == 1


def test_read_manifest_filters_and_limit(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    library.mkdir()
    records = [
        {"id": "000000000001", "status": "succeeded", "source_kind": "local", "source": "a.mp4"},
        {"id": "000000000002", "status": "failed", "source_kind": "url", "source": "https://example.com/b"},
        {"id": "000000000003", "status": "succeeded", "source_kind": "url", "source": "https://example.com/c"},
    ]
    (library / MANIFEST_NAME).write_text(
        "\n".join(json.dumps(rec) for rec in records) + "\n",
        encoding="utf-8",
    )
    manager = BatchManager()
    all_rows = manager.read_manifest(str(library))
    assert all_rows["count"] == 3
    only_ok = manager.read_manifest(str(library), status="succeeded")
    assert only_ok["count"] == 2
    only_url = manager.read_manifest(str(library), source_kind="url")
    assert only_url["count"] == 2
    limited = manager.read_manifest(str(library), status="succeeded", limit=1)
    assert limited["count"] == 1
    assert limited["records"][0]["id"] == "000000000001"
    empty = manager.read_manifest(str(tmp_path / "missing"))
    assert empty["count"] == 0
    assert empty["records"] == []
    with pytest.raises(InputError, match="limit"):
        manager.read_manifest(str(library), limit=0)


def test_submit_transcribe_without_whisper_fails_fast(tmp_path: Path, monkeypatch) -> None:
    import media_intake.batches as batches

    monkeypatch.setattr(batches, "whisper_is_installed", lambda: False)
    manager = BatchManager(run_batch_fn=_fake_run())
    with pytest.raises(DependencyError, match="media-intake\\[whisper\\]"):
        manager.submit_batch(["clip.mp4"], str(tmp_path / "lib"), transcribe="missing")
