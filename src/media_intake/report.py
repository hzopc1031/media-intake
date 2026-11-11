from __future__ import annotations

from media_intake.models import JobResult, RunReport


def format_report(report: RunReport) -> str:
    lines = [
        "运行报告",
        f"  成功：{len(report.succeeded)}    跳过：{len(report.skipped)}    失败：{len(report.failed)}",
    ]
    if report.succeeded:
        lines.append("")
        lines.append("成功：")
        for job in report.succeeded:
            lines.extend(_item_lines(job))
    if report.skipped:
        lines.append("")
        lines.append("跳过（已完成，本次未重新处理）：")
        for job in report.skipped:
            lines.extend(_item_lines(job, include_error=False))
    if report.failed:
        lines.append("")
        lines.append("失败：")
        for job in report.failed:
            lines.extend(_item_lines(job, include_error=True))
    lines.append("")
    return "\n".join(lines)


def _item_lines(job: JobResult, *, include_error: bool = False) -> list[str]:
    title = job.title or "(无标题)"
    lines = [f"  - [{job.id}] {title}"]
    lines.append(f"      来源：{job.source}")
    if job.media_path:
        lines.append(f"      媒体：{job.media_path}")
    if job.subtitle_path:
        kind = f"（{job.subtitle_kind.value}）" if job.subtitle_kind else ""
        lines.append(f"      字幕：{job.subtitle_path}{kind}")
    elif job.status.value == "succeeded":
        lines.append("      字幕：无来源字幕")
    if job.transcript_path:
        lines.append(f"      转写：{job.transcript_path}")
    if include_error and job.error:
        lines.append(f"      原因：{job.error}")
    return lines
