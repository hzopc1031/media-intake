from __future__ import annotations

import json
from pathlib import Path

from media_intake.config import BatchOptions
from media_intake.extractors import default_registry
from media_intake.inputs import classify, source_id
from media_intake.models import JobStatus, SourceKind
from media_intake.pipeline import run_batch
from media_intake.transcribe import TranscriptResult, Transcriber
from tests.fakes import FakeRunner


def test_expand_creates_individual_jobs_and_resume_skips(tmp_path: Path) -> None:
    playlist = "https://example.com/playlist/p"
    videos = [
        "https://example.com/v/one",
        "https://example.com/v/two",
        "https://example.com/v/three",
    ]
    runner = FakeRunner(
        playlist={
            playlist: [
                {"webpage_url": url, "title": url.rsplit("/", 1)[-1], "duration": 4}
                for url in videos
            ]
        }
    )
    library = tmp_path / "lib"
    options = BatchOptions(
        library_root=library,
        expand=True,
        retries=0,
        retry_wait=0,
        concurrency=2,
    )
    backends = default_registry(Path("yt-dlp"))
    report = run_batch([playlist], options, runner=runner, backends=backends)
    assert not report.failed
    assert len(report.succeeded) == 3
    assert {job.source for job in report.succeeded} == set(videos)
    assert all(job.parent_source == playlist for job in report.succeeded)
    assert runner.downloads == videos or set(runner.downloads) == set(videos)

    manifest_lines = (library / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest_lines) == 3
    records = [json.loads(line) for line in manifest_lines]
    assert all(row["parent_source"] == playlist for row in records)
    assert all(row["subtitle_path"].endswith("source.srt") for row in records)
    assert "素材索引" in (library / "index.md").read_text(encoding="utf-8")

    # 再次运行应跳过全部三条，不再下载
    runner.downloads.clear()
    report2 = run_batch([playlist], options, runner=runner, backends=backends)
    assert len(report2.skipped) == 3
    assert not report2.succeeded
    assert not report2.failed
    assert runner.downloads == []


def test_expand_max_items_and_expand_failure(tmp_path: Path) -> None:
    playlist = "https://example.com/playlist/p"
    runner = FakeRunner(
        playlist={
            playlist: [
                {"webpage_url": f"https://example.com/v/{i}", "title": str(i)}
                for i in range(5)
            ]
        }
    )
    options = BatchOptions(
        library_root=tmp_path / "lib1",
        expand=True,
        max_items=2,
        retries=0,
        retry_wait=0,
    )
    report = run_batch([playlist], options, runner=runner, backends=default_registry(Path("yt-dlp")))
    assert len(report.succeeded) == 2

    bad = "https://example.com/playlist/missing"
    runner2 = FakeRunner(fail_expand={bad})
    report2 = run_batch(
        [bad],
        BatchOptions(library_root=tmp_path / "lib2", expand=True, retries=0, retry_wait=0),
        runner=runner2,
        backends=default_registry(Path("yt-dlp")),
    )
    assert len(report2.failed) == 1
    assert report2.failed[0].source == bad
    assert "展开" in (report2.failed[0].error or "")
    assert report2.exit_code == 1


def test_failed_then_retry_and_force(tmp_path: Path) -> None:
    url = "https://example.com/v/x"
    runner = FakeRunner(fail_urls={url})
    options = BatchOptions(
        library_root=tmp_path / "lib",
        retries=0,
        retry_wait=0,
    )
    backends = default_registry(Path("yt-dlp"))
    report = run_batch([url], options, runner=runner, backends=backends)
    assert report.exit_code == 1
    assert report.failed[0].source == url

    runner.fail_urls.clear()
    report2 = run_batch([url], options, runner=runner, backends=backends)
    assert report2.exit_code == 0
    assert len(report2.succeeded) == 1

    before = len(runner.downloads)
    report3 = run_batch([url], options, runner=runner, backends=backends)
    assert len(report3.skipped) == 1
    assert len(runner.downloads) == before

    forced = BatchOptions(library_root=options.library_root, retries=0, retry_wait=0, force=True)
    report4 = run_batch([url], forced, runner=runner, backends=backends)
    assert len(report4.succeeded) == 1
    assert len(runner.downloads) == before + 1


def test_retries_use_backoff_and_succeed(tmp_path: Path) -> None:
    url = "https://example.com/v/flaky"
    runner = FakeRunner(fail_urls={url})
    # 第一次失败后从 fail_urls 里拿掉
    original = runner._ytdlp

    def once_then_ok(argv, cwd):
        result = original(argv, cwd)
        if argv[-1] == url and "--flat-playlist" not in argv and url in runner.fail_urls:
            runner.fail_urls.discard(url)
        return result

    runner._ytdlp = once_then_ok  # type: ignore[method-assign]
    runner.fail_urls.add(url)
    report = run_batch(
        [url],
        BatchOptions(library_root=tmp_path / "lib", retries=2, retry_wait=0),
        runner=runner,
        backends=default_registry(Path("yt-dlp")),
    )
    assert report.exit_code == 0
    assert report.succeeded[0].attempts == 2


class _DummyTranscriber:
    def available(self) -> bool:
        return True

    def transcribe(self, media_path: Path, dest_dir: Path) -> TranscriptResult:
        srt = dest_dir / "produced.srt"
        txt = dest_dir / "produced.txt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n转写\n", encoding="utf-8")
        txt.write_text("转写\n", encoding="utf-8")
        return TranscriptResult(srt_path=srt, text_path=txt, engine="dummy")


def test_transcriber_hook_used_when_no_source_subs(tmp_path: Path) -> None:
    media = tmp_path / "talk.mp4"
    media.write_bytes(b"not-real")
    # 走本地归档，但 ffprobe 被 FakeRunner 接管；需要真实 ffmpeg for archive? 
    # archive doesn't call ffmpeg, only ffprobe via runner. File just needs to exist.
    # But require_ffmpeg uses shutil.which then runner for version - ok if ffmpeg exists.
    options = BatchOptions(
        library_root=tmp_path / "lib",
        retries=0,
        transcriber=_DummyTranscriber(),  # type: ignore[arg-type]
    )
    runner = FakeRunner()
    report = run_batch([str(media)], options, runner=runner)
    assert report.exit_code == 0
    job = report.succeeded[0]
    assert job.transcript_path and job.transcript_path.endswith("transcript.srt")
    assert (options.library_root / job.transcript_path).read_text(encoding="utf-8").find("转写") >= 0


def test_collect_source_id_stable() -> None:
    a = classify("https://example.com/v/1?utm_source=x")
    b = classify("https://example.com/v/1")
    assert source_id(a) == source_id(b)
    assert a.kind == SourceKind.URL


# 让类型检查知道 Dummy 满足协议
assert isinstance(_DummyTranscriber(), Transcriber)
