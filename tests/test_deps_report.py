from pathlib import Path

from media_intake.deps import doctor_text, format_doctor_report, inspect_tool, update_ytdlp
from media_intake.models import JobResult, JobStatus, RunReport, SourceKind
from media_intake.process import CommandResult
from media_intake.report import format_report


class _MissingRunner:
    def run(self, argv, *, cwd=None, timeout=None) -> CommandResult:
        return CommandResult(list(argv), 1, "", "cannot run")


def test_doctor_report_ok_and_missing() -> None:
    from media_intake.deps import ToolInfo

    ok = ToolInfo("ffmpeg", Path("/usr/bin/ffmpeg"), "7.1", True, "/usr/bin/ffmpeg")
    bad = ToolInfo("yt-dlp", None, None, False, "未找到 yt-dlp")
    text = format_doctor_report([ok, bad])
    assert "[ok] ffmpeg" in text
    assert "[缺失] yt-dlp" in text
    assert "未找到 yt-dlp" in text


def test_doctor_and_update_use_runner() -> None:
    text, code = doctor_text()
    # 本机 CI / 开发环境应能找到依赖；即使缺了也不应抛异常
    assert "依赖自检" in text
    assert code in {0, 1}

    class PipOk:
        def run(self, argv, *, cwd=None, timeout=None) -> CommandResult:
            argv = list(argv)
            if argv[-1] in {"--version", "-version"}:
                return CommandResult(argv, 0, "2026.10.26\n", "")
            if "pip" in argv:
                return CommandResult(argv, 0, "Successfully installed yt-dlp\n", "")
            return CommandResult(argv, 0, "ok\n", "")

    text, code = update_ytdlp(PipOk())
    assert code == 0
    assert "pip" in text


def test_report_lists_reasons_and_exit_code() -> None:
    ok = JobResult(
        id="1",
        source="https://example.com/a",
        source_kind=SourceKind.URL,
        status=JobStatus.SUCCEEDED,
        title="A",
        media_path="items/1/media.mp4",
    )
    skipped = JobResult(
        id="2",
        source="https://example.com/b",
        source_kind=SourceKind.URL,
        status=JobStatus.SKIPPED,
        title="B",
    )
    failed = JobResult(
        id="3",
        source="missing.mp4",
        source_kind=SourceKind.LOCAL,
        status=JobStatus.FAILED,
        title=None,
        error="本地文件不存在：missing.mp4",
    )
    report = RunReport(succeeded=[ok], skipped=[skipped], failed=[failed])
    text = format_report(report)
    assert "成功：1" in text
    assert "跳过：1" in text
    assert "失败：1" in text
    assert "本地文件不存在" in text
    assert report.exit_code == 1
    assert RunReport(succeeded=[ok]).exit_code == 0


def test_inspect_tool_missing(monkeypatch) -> None:
    import media_intake.deps as deps

    monkeypatch.setattr(deps, "find_executable", lambda name: None)
    info = inspect_tool("yt-dlp", ("--version",), _MissingRunner())
    assert not info.ok
    assert "pip" in info.detail or "yt-dlp" in info.detail
