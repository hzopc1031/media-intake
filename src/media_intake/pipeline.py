from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from media_intake.config import BatchOptions
from media_intake.deps import require_ffmpeg, require_ytdlp
from media_intake.download import pick_media_file, pick_subtitle_files
from media_intake.errors import DependencyError, InputError, IntakeError, JobError
from media_intake.extractors import BackendRegistry, default_registry
from media_intake.inputs import classify, source_id
from media_intake.library import Library, utc_now
from media_intake.models import JobResult, JobStatus, RunReport, Source, SourceKind, SubtitleKind
from media_intake.probe import archive_local_file, ffprobe_media
from media_intake.process import CommandRunner, SubprocessRunner
from media_intake.store import JobStore
from media_intake.subtitles import find_sidecar_subtitles, write_plain_text
from media_intake.metadata import fields_from_info
from media_intake.transcribe import (
    FasterWhisperTranscriber,
    TranscribeMode,
    TranscriptResult,
    WHISPER_INSTALL_HINT,
    should_transcribe,
    whisper_is_installed,
)


def run_batch(
    raw_items: list[str],
    options: BatchOptions,
    *,
    runner: CommandRunner | None = None,
    cwd: Path | None = None,
    backends: BackendRegistry | None = None,
) -> RunReport:
    """处理一批链接或本地文件。命令行与后续 MCP 共用此入口。"""
    runner = runner or SubprocessRunner()
    _prepare_transcriber(options)
    library = Library(options.library_root)
    library.prepare()
    store = JobStore(library.state_path)

    needs_url = _inputs_need_url(raw_items, cwd)
    require_ffmpeg(runner)
    ytdlp: Path | None = require_ytdlp(runner) if needs_url else None
    if backends is None and ytdlp is not None:
        backends = default_registry(ytdlp)

    sources, expand_failures = collect_sources(
        raw_items,
        options,
        runner=runner,
        cwd=cwd,
        backends=backends,
    )

    to_run: list[tuple[str, Source]] = []
    skipped: list[JobResult] = []
    for job_id, source in sources:
        existing = store.get(job_id)
        if existing and existing.status == JobStatus.SUCCEEDED and not options.force:
            existing.status = JobStatus.SKIPPED
            skipped.append(existing)
            continue
        to_run.append((job_id, source))

    succeeded: list[JobResult] = []
    failed: list[JobResult] = list(expand_failures)
    cancelled: list[JobResult] = []

    def _process(job_id: str, source: Source) -> JobResult:
        return _run_job_with_retry(
            job_id,
            source,
            options=options,
            library=library,
            store=store,
            runner=runner,
            backends=backends,
        )

    if to_run and _is_cancelled(options):
        now = utc_now()
        for job_id, source in to_run:
            result = JobResult(
                id=job_id,
                source=source.raw,
                source_kind=source.kind,
                status=JobStatus.CANCELLED,
                error="批次已取消",
                attempts=0,
                parent_source=source.parent_source,
            )
            store.upsert(result, created_at=now, updated_at=now, finished_at=now)
            cancelled.append(result)
        to_run = []

    if to_run:
        workers = min(options.concurrency, len(to_run))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process, job_id, source): job_id for job_id, source in to_run}
            for future in as_completed(futures):
                result = future.result()
                if result.status == JobStatus.SUCCEEDED:
                    succeeded.append(result)
                elif result.status == JobStatus.CANCELLED:
                    cancelled.append(result)
                else:
                    failed.append(result)

    store_all = store.list_all()
    library.rewrite_outputs(store_all)
    order = {job_id: i for i, (job_id, _) in enumerate(sources)}
    succeeded.sort(key=lambda j: order.get(j.id, 0))
    failed.sort(key=lambda j: (0 if j.id in order else 1, order.get(j.id, 0)))
    skipped.sort(key=lambda j: order.get(j.id, 0))
    cancelled.sort(key=lambda j: order.get(j.id, 0))
    return RunReport(succeeded=succeeded, skipped=skipped, failed=failed, cancelled=cancelled)


def collect_sources(
    raw_items: list[str],
    options: BatchOptions,
    *,
    runner: CommandRunner,
    cwd: Path | None,
    backends: BackendRegistry | None,
) -> tuple[list[tuple[str, Source]], list[JobResult]]:
    """把输入变成队列条目。开启 expand 时，播放列表/主页会拆成单条视频。"""
    sources: list[tuple[str, Source]] = []
    seen: set[str] = set()
    expand_failures: list[JobResult] = []

    def _add(source: Source) -> None:
        job_id = source_id(source)
        if job_id in seen:
            return
        seen.add(job_id)
        sources.append((job_id, source))

    for raw in raw_items:
        try:
            source = classify(raw, cwd=cwd)
        except InputError as exc:
            expand_failures.append(
                JobResult(
                    id=source_id(
                        Source(raw=raw, kind=SourceKind.URL, identity=raw)
                    ),
                    source=raw,
                    source_kind=SourceKind.URL,
                    status=JobStatus.FAILED,
                    error=str(exc),
                    attempts=0,
                )
            )
            continue
        if source.kind == SourceKind.URL and options.expand:
            if backends is None:
                expand_failures.append(
                    _expand_failure(source, "展开链接需要 yt-dlp，但未找到可执行文件")
                )
                continue
            backend = backends.choose(source.raw)
            try:
                extracted = backend.expand(
                    source.raw, options, runner, max_items=options.max_items
                )
            except Exception as exc:
                message = str(exc) if isinstance(exc, IntakeError) else f"{type(exc).__name__}: {exc}"
                expand_failures.append(_expand_failure(source, message))
                continue
            if not extracted:
                expand_failures.append(
                    _expand_failure(source, "展开播放列表或主页后没有得到可用的条目")
                )
                continue
            for item in extracted:
                try:
                    child = classify(item.url, cwd=cwd)
                except InputError:
                    continue
                if child.kind != SourceKind.URL:
                    continue
                _add(
                    Source(
                        raw=item.url,
                        kind=SourceKind.URL,
                        identity=child.identity,
                        parent_source=source.raw,
                    )
                )
        else:
            _add(source)
    return sources, expand_failures


def _inputs_need_url(raw_items: list[str], cwd: Path | None) -> bool:
    for raw in raw_items:
        try:
            if classify(raw, cwd=cwd).kind == SourceKind.URL:
                return True
        except InputError:
            continue
    return False


def _expand_failure(source: Source, message: str) -> JobResult:
    return JobResult(
        id=source_id(source),
        source=source.raw,
        source_kind=source.kind,
        status=JobStatus.FAILED,
        error=message,
        attempts=0,
    )


def _run_job_with_retry(
    job_id: str,
    source: Source,
    *,
    options: BatchOptions,
    library: Library,
    store: JobStore,
    runner: CommandRunner,
    backends: BackendRegistry | None,
) -> JobResult:
    attempts_allowed = options.retries + 1
    last: JobResult | None = None
    if _is_cancelled(options):
        now = utc_now()
        result = JobResult(
            id=job_id,
            source=source.raw,
            source_kind=source.kind,
            status=JobStatus.CANCELLED,
            error="批次已取消",
            attempts=0,
            parent_source=source.parent_source,
        )
        store.upsert(result, created_at=now, updated_at=now, finished_at=now)
        library.rewrite_outputs(store.list_all(), updated_at=now)
        return result
    for attempt in range(1, attempts_allowed + 1):
        started = utc_now()
        running = JobResult(
            id=job_id,
            source=source.raw,
            source_kind=source.kind,
            status=JobStatus.RUNNING,
            attempts=attempt,
            parent_source=source.parent_source,
        )
        store.upsert(running, created_at=started, updated_at=started, started_at=started)
        try:
            result = process_job(
                job_id,
                source,
                options=options,
                library=library,
                runner=runner,
                backends=backends,
            )
            result.attempts = attempt
            result.parent_source = source.parent_source
            finished = utc_now()
            store.upsert(result, created_at=started, updated_at=finished, finished_at=finished)
            library.rewrite_outputs(store.list_all(), updated_at=finished)
            return result
        except Exception as exc:
            message = str(exc) if isinstance(exc, IntakeError) else f"{type(exc).__name__}: {exc}"
            last = JobResult(
                id=job_id,
                source=source.raw,
                source_kind=source.kind,
                status=JobStatus.FAILED,
                error=message,
                attempts=attempt,
                parent_source=source.parent_source,
            )
            finished = utc_now()
            store.upsert(last, created_at=started, updated_at=finished, finished_at=finished)
            library.rewrite_outputs(store.list_all(), updated_at=finished)
            if attempt < attempts_allowed:
                if _sleep_interruptible(options, options.retry_wait * (2 ** (attempt - 1))):
                    last.status = JobStatus.CANCELLED
                    last.error = "批次已取消"
                    finished = utc_now()
                    store.upsert(last, created_at=started, updated_at=finished, finished_at=finished)
                    library.rewrite_outputs(store.list_all(), updated_at=finished)
                    return last
    assert last is not None
    return last


def process_job(
    job_id: str,
    source: Source,
    *,
    options: BatchOptions,
    library: Library,
    runner: CommandRunner,
    backends: BackendRegistry | None,
) -> JobResult:
    dest = library.item_dir(job_id)
    dest.mkdir(parents=True, exist_ok=True)
    if source.kind == SourceKind.LOCAL:
        result = _process_local(job_id, source, dest, options, library, runner)
    else:
        if backends is None:
            raise JobError("处理链接需要 yt-dlp，但未找到可执行文件")
        result = _process_url(job_id, source, dest, options, library, runner, backends)
    result.parent_source = source.parent_source
    return result


def _process_local(
    job_id: str,
    source: Source,
    dest: Path,
    options: BatchOptions,
    library: Library,
    runner: CommandRunner,
) -> JobResult:
    src_path = Path(source.identity)
    if not src_path.is_file():
        raise JobError(f"本地文件不存在：{source.raw}")
    _, ffprobe = require_ffmpeg(runner)
    media = archive_local_file(src_path, dest, reference=options.reference_local)
    meta = ffprobe_media(ffprobe, media, runner)
    title = meta.get("title") or src_path.stem
    subtitle_path, subtitle_text, kind = _ingest_subtitles(
        dest,
        extra_sources=find_sidecar_subtitles(src_path),
        preferred_langs=_lang_list(options.sub_langs),
    )
    produced = _maybe_transcribe(
        media, dest, options, has_source_subs=subtitle_path is not None
    )
    return _job_succeeded(
        job_id,
        source,
        library=library,
        dest=dest,
        media=media,
        title=title,
        duration=meta.get("duration_seconds"),
        subtitle_path=subtitle_path,
        subtitle_text=subtitle_text,
        kind=kind,
        produced=produced,
    )


def _process_url(
    job_id: str,
    source: Source,
    dest: Path,
    options: BatchOptions,
    library: Library,
    runner: CommandRunner,
    backends: BackendRegistry,
) -> JobResult:
    backend = backends.choose(source.raw)
    info = backend.download(source.raw, dest, options, runner)
    media = pick_media_file(dest)
    if media is None:
        raise JobError("下载完成但未找到媒体文件")
    title = _info_title(info) or media.stem
    duration = _info_duration(info)
    if duration is None:
        _, ffprobe = require_ffmpeg(runner)
        duration = ffprobe_media(ffprobe, media, runner).get("duration_seconds")
    subtitle_path, subtitle_text, kind = _ingest_subtitles(
        dest,
        extra_sources=pick_subtitle_files(dest),
        preferred_langs=_lang_list(options.sub_langs),
        auto_hint=options.write_auto_subs,
    )
    produced = _maybe_transcribe(
        media, dest, options, has_source_subs=subtitle_path is not None
    )
    extra = fields_from_info(info)
    thumbnail = _pick_thumbnail(dest)
    return _job_succeeded(
        job_id,
        source,
        library=library,
        dest=dest,
        media=media,
        title=title,
        duration=duration,
        subtitle_path=subtitle_path,
        subtitle_text=subtitle_text,
        kind=kind,
        produced=produced,
        platform=extra.get("platform"),
        uploader=extra.get("uploader"),
        upload_date=extra.get("upload_date"),
        thumbnail_path=library.rel(thumbnail) if thumbnail else None,
    )



def _prepare_transcriber(options: BatchOptions) -> None:
    if options.transcribe_mode is TranscribeMode.NEVER:
        return
    if options.transcriber is None:
        options.transcriber = build_transcriber(options)
    if not options.transcriber.available():
        raise DependencyError(_missing_transcriber_hint(options.asr_backend))


def build_transcriber(options: BatchOptions):
    backend = getattr(options, "asr_backend", "faster-whisper") or "faster-whisper"
    if backend == "whisperx":
        from media_intake.whisperx_backend import WHISPERX_INSTALL_HINT, WhisperXTranscriber, whisperx_is_installed

        if not whisperx_is_installed():
            raise DependencyError(WHISPERX_INSTALL_HINT)
        return WhisperXTranscriber(
            model=options.whisper_model,
            device=options.whisper_device,
            compute_type=options.whisper_compute_type,
            language=options.whisper_language,
            diarize=bool(getattr(options, "diarize", False)),
        )
    if not whisper_is_installed():
        raise DependencyError(WHISPER_INSTALL_HINT)
    return FasterWhisperTranscriber(
        model=options.whisper_model,
        device=options.whisper_device,
        compute_type=options.whisper_compute_type,
        language=options.whisper_language,
    )


def _missing_transcriber_hint(backend: str) -> str:
    if backend == "whisperx":
        from media_intake.whisperx_backend import WHISPERX_INSTALL_HINT

        return WHISPERX_INSTALL_HINT
    return WHISPER_INSTALL_HINT


def _maybe_transcribe(
    media: Path,
    dest: Path,
    options: BatchOptions,
    *,
    has_source_subs: bool,
) -> TranscriptResult | None:
    if not should_transcribe(
        options.transcribe_mode,
        has_source_subs=has_source_subs,
        media_path=media,
    ):
        return None
    transcriber = options.transcriber
    if transcriber is None or not transcriber.available():
        raise JobError(_missing_transcriber_hint(getattr(options, "asr_backend", "faster-whisper")))
    produced = transcriber.transcribe(media, dest)
    transcript_path = dest / "transcript.srt"
    transcript_text = dest / "transcript.txt"
    if produced.srt_path.resolve() != transcript_path.resolve():
        transcript_path.write_bytes(produced.srt_path.read_bytes())
    if produced.text_path.resolve() != transcript_text.resolve():
        transcript_text.write_bytes(produced.text_path.read_bytes())
    elif not transcript_text.exists():
        write_plain_text(transcript_path, transcript_text)
    words_path = dest / "transcript.words.json"
    if produced.words_path is not None:
        if produced.words_path.resolve() != words_path.resolve():
            words_path.write_bytes(produced.words_path.read_bytes())
    else:
        words_path = None
    return TranscriptResult(
        srt_path=transcript_path,
        text_path=transcript_text,
        engine=produced.engine,
        language=produced.language,
        model=produced.model,
        words_path=words_path,
        diarized=bool(produced.diarized),
    )


def _job_succeeded(
    job_id: str,
    source,
    *,
    library: Library,
    dest: Path,
    media: Path,
    title: str | None,
    duration: float | None,
    subtitle_path: Path | None,
    subtitle_text: Path | None,
    kind,
    produced: TranscriptResult | None,
    platform: str | None = None,
    uploader: str | None = None,
    upload_date: str | None = None,
    thumbnail_path: str | None = None,
) -> JobResult:
    return JobResult(
        id=job_id,
        source=source.raw,
        source_kind=source.kind,
        status=JobStatus.SUCCEEDED,
        title=title,
        duration_seconds=duration,
        item_dir=library.rel(dest),
        media_path=library.rel(media),
        subtitle_path=library.rel(subtitle_path),
        subtitle_text_path=library.rel(subtitle_text),
        subtitle_kind=kind,
        transcript_path=library.rel(produced.srt_path) if produced else None,
        transcript_text_path=library.rel(produced.text_path) if produced else None,
        transcript_model=produced.model if produced else None,
        transcript_language=produced.language if produced else None,
        transcript_engine=produced.engine if produced else None,
        transcript_words_path=library.rel(produced.words_path) if produced and produced.words_path else None,
        transcript_diarized=produced.diarized if produced else None,
        parent_source=source.parent_source,
        platform=platform,
        uploader=uploader,
        upload_date=upload_date,
        thumbnail_path=thumbnail_path,
    )


def _pick_thumbnail(dest: Path) -> Path | None:
    for path in sorted(dest.glob("thumbnail.*")) + sorted(dest.glob("*.jpg")) + sorted(dest.glob("*.webp")):
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            if path.name.startswith("media."):
                continue
            return path
    return None


def _ingest_subtitles(
    dest: Path,
    *,
    extra_sources: list[Path],
    preferred_langs: list[str],
    auto_hint: bool = False,
) -> tuple[Path | None, Path | None, SubtitleKind | None]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    for path in extra_sources:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        candidates.append(path)
    if not candidates:
        return None, None, None
    chosen = _choose_subtitle(candidates, preferred_langs)
    source_srt = dest / "source.srt"
    source_txt = dest / "source.txt"
    if chosen.suffix.lower() == ".srt":
        if chosen.resolve() != source_srt.resolve():
            source_srt.write_bytes(chosen.read_bytes())
    else:
        source_srt.write_bytes(chosen.read_bytes())
    write_plain_text(source_srt, source_txt)
    kind = SubtitleKind.AUTO if auto_hint and _looks_auto(chosen.name) else SubtitleKind.SOURCE
    if _looks_auto(chosen.name):
        kind = SubtitleKind.AUTO
    return source_srt, source_txt, kind


def _choose_subtitle(files: list[Path], preferred_langs: list[str]) -> Path:
    if not preferred_langs:
        srt = [p for p in files if p.suffix.lower() == ".srt"]
        return (srt or files)[0]
    lowered = [lang.lower() for lang in preferred_langs]
    for lang in lowered:
        for path in files:
            name = path.name.lower()
            if f".{lang}." in name or name.endswith(f".{lang}{path.suffix.lower()}"):
                return path
    srt = [p for p in files if p.suffix.lower() == ".srt"]
    return (srt or files)[0]


def _looks_auto(name: str) -> bool:
    lower = name.lower()
    return "auto" in lower or "automatic" in lower


def _lang_list(spec: str | None) -> list[str]:
    if not spec:
        return []
    return [part.strip() for part in spec.split(",") if part.strip()]


def _info_title(info: dict) -> str | None:
    title = info.get("title") or info.get("fulltitle")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


def _info_duration(info: dict) -> float | None:
    value = info.get("duration")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_cancelled(options: BatchOptions) -> bool:
    event = getattr(options, "cancel_event", None)
    return event is not None and bool(getattr(event, "is_set", lambda: False)())


def _sleep_interruptible(options: BatchOptions, seconds: float) -> bool:
    """等待重试间隔；若批次被取消则立刻返回 True。"""
    if seconds <= 0:
        return _is_cancelled(options)
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _is_cancelled(options):
            return True
        time.sleep(min(0.1, max(0.0, deadline - time.time())))
    return _is_cancelled(options)
