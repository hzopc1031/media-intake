"""把外部工具已下载的本地文件导入素材库。"""

from __future__ import annotations

from pathlib import Path

from media_intake.config import BatchOptions
from media_intake.errors import InputError
from media_intake.inputs import classify, source_id
from media_intake.library import Library, utc_now
from media_intake.models import JobResult, JobStatus, RunReport, Source, SourceKind
from media_intake.pipeline import _process_local, _prepare_transcriber
from media_intake.process import CommandRunner, SubprocessRunner
from media_intake.store import JobStore


def import_local_media(
    file: Path,
    options: BatchOptions,
    *,
    source_url: str | None = None,
    title: str | None = None,
    uploader: str | None = None,
    platform: str | None = None,
    upload_date: str | None = None,
    runner: CommandRunner | None = None,
) -> RunReport:
    """把本地媒体文件归档进素材库，并可附上来源链接和标题等元数据。"""
    path = Path(file).expanduser().resolve()
    if not path.is_file():
        raise InputError(f"本地文件不存在：{path}")
    if source_url:
        logical = classify(source_url.strip())
        if logical.kind is not SourceKind.URL:
            raise InputError("--source-url 必须是 http(s) 链接")
    else:
        logical = classify(str(path))
    job_id = source_id(logical)
    _prepare_transcriber(options)
    library = Library(options.library_root)
    library.prepare()
    store = JobStore(library.state_path)
    existing = store.get(job_id)
    if existing and existing.status == JobStatus.SUCCEEDED and not options.force:
        existing.status = JobStatus.SKIPPED
        library.rewrite_outputs(store.list_all())
        return RunReport(skipped=[existing])
    local_source = Source(raw=str(path), kind=SourceKind.LOCAL, identity=str(path))
    result = _process_local(
        job_id,
        local_source,
        library.item_dir(job_id),
        options,
        library,
        runner or SubprocessRunner(),
    )
    result.source = logical.raw
    result.source_kind = logical.kind
    if title and title.strip():
        result.title = title.strip()
    if uploader and uploader.strip():
        result.uploader = uploader.strip()
    if platform and platform.strip():
        result.platform = platform.strip()
    if upload_date and upload_date.strip():
        result.upload_date = upload_date.strip()
    finished = utc_now()
    store.upsert(result, created_at=finished, updated_at=finished, finished_at=finished)
    library.rewrite_outputs(store.list_all(), updated_at=finished)
    return RunReport(succeeded=[result])
