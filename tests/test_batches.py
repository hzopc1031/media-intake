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
        if last["status"] in {"succeeded", "completed", "failed", "cancelled"}:
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


def test_submit_detach_stays_queued_until_worker(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    manager = BatchManager(run_batch_fn=_fake_run(), detach=True)
    submitted = manager.submit_batch(["clip.mp4"], str(library))
    assert submitted["detach"] is True
    assert submitted["status"] == "queued"
    status = manager.get_status(submitted["batch_id"], str(library))
    assert status["status"] == "queued"
    from media_intake.worker import run_worker

    code = run_worker(library, once=True, run_batch_fn=_fake_run())
    assert code == 0
    status = manager.get_status(submitted["batch_id"], str(library))
    assert status["status"] == "succeeded"
    assert status["counts"]["succeeded"] == 1


def test_cancel_queued_batch(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    manager = BatchManager(run_batch_fn=_fake_run(), detach=True)
    submitted = manager.submit_batch(["clip.mp4"], str(library))
    cancelled = manager.cancel_batch(submitted["batch_id"], str(library))
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_requested"] is True
    listed = manager.list_batches(str(library))
    assert listed["count"] == 1
    assert listed["records"][0]["status"] == "cancelled"
    # worker should not run a cancelled queued batch
    from media_intake.worker import run_worker

    run_worker(library, once=True, run_batch_fn=_fake_run())
    status = manager.get_status(submitted["batch_id"], str(library))
    assert status["status"] == "cancelled"


def test_cancel_running_inprocess_batch(tmp_path: Path) -> None:
    import threading

    started = threading.Event()
    release = threading.Event()

    def run(items, options, **kwargs):
        started.set()
        # 等待外部取消
        assert options.cancel_event is not None
        release.wait(timeout=2)
        # pipeline 会在任务开始前检查；这里模拟长任务注意到取消
        if options.cancel_event.is_set():
            from media_intake.models import JobResult, JobStatus, SourceKind

            job = JobResult(
                id="aabbccddeefe",
                source=items[0],
                source_kind=SourceKind.LOCAL,
                status=JobStatus.CANCELLED,
                error="批次已取消",
            )
            return RunReport(cancelled=[job])
        return _fake_run()(items, options, **kwargs)

    manager = BatchManager(run_batch_fn=run, detach=False)
    submitted = manager.submit_batch(["clip.mp4"], str(tmp_path / "lib"))
    assert started.wait(timeout=2)
    cancelled = manager.cancel_batch(submitted["batch_id"], str(tmp_path / "lib"))
    assert cancelled["cancel_requested"] is True
    release.set()
    status = _wait(manager, submitted["batch_id"], tmp_path / "lib")
    assert status["status"] == "cancelled"
    assert status["counts"]["cancelled"] == 1


def test_worker_reclaims_stale_running_batch(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    manager = BatchManager(run_batch_fn=_fake_run(), detach=True)
    submitted = manager.submit_batch(["clip.mp4"], str(library))
    rec_path = library / ".media-intake" / "batches" / f"{submitted['batch_id']}.json"
    # 模拟崩溃：状态为 running，心跳已过期
    from media_intake.batches import load_record, persist_record

    rec = load_record(library, submitted["batch_id"])
    assert rec is not None
    rec["status"] = "running"
    rec["claimed_by"] = "dead-worker"
    rec["claimed_at"] = "2020-01-01T00:00:00Z"
    rec["heartbeat_at"] = "2020-01-01T00:00:00Z"
    persist_record(rec)
    from media_intake.worker import run_worker

    run_worker(library, once=True, stale_after=1.0, run_batch_fn=_fake_run())
    status = manager.get_status(submitted["batch_id"], str(library))
    assert status["status"] == "succeeded"
    assert rec_path.is_file()


def test_cli_submit_status_cancel_and_worker(tmp_path: Path) -> None:
    from media_intake.cli import main

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"not-a-real-media")
    # submit 本地不存在的媒体也会入队；worker 用真实 pipeline 会失败。
    # 这里只检查入队、查询和取消排队中的批次。
    library = tmp_path / "lib"
    code = main(["submit", str(media), "--library", str(library), "--retries", "0"])
    assert code == 0
    from media_intake.batches import BatchManager

    listed = BatchManager().list_batches(str(library))
    assert listed["count"] == 1
    batch_id = listed["records"][0]["batch_id"]
    assert listed["records"][0]["status"] == "queued"
    code = main(["status", batch_id, "--library", str(library)])
    assert code == 0
    code = main(["cancel", batch_id, "--library", str(library)])
    assert code == 0
    status = BatchManager().get_status(batch_id, str(library))
    assert status["status"] == "cancelled"
