"""可选的 whisperX 转写后端。未安装 whisperx 时也可安全导入。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from media_intake.errors import DependencyError, JobError
from media_intake.subtitles import write_plain_text
from media_intake.transcribe import (
    TranscriptResult,
    decode_media_to_pcm,
    format_srt_timestamp,
    resolve_compute_type,
    resolve_device,
)

WHISPERX_INSTALL_HINT = (
    "未安装 whisperx，无法使用 whisperX 转写后端。这是可选功能，请先安装：\n"
    f"  {sys.executable} -m pip install 'media-intake[whisperx]'\n"
    "或：\n"
    f"  {sys.executable} -m pip install whisperx"
)

DIARIZE_TOKEN_HINT = (
    "说话人分离需要 Hugging Face 访问令牌（pyannote 模型）。"
    "请设置环境变量 MEDIA_INTAKE_HF_TOKEN、HF_TOKEN 或 HUGGING_FACE_HUB_TOKEN，"
    "不要把令牌写进命令行、配置文件或仓库。"
)


def whisperx_is_installed() -> bool:
    try:
        import whisperx  # noqa: F401
    except ImportError:
        return False
    return True


def hf_token() -> str | None:
    for key in ("MEDIA_INTAKE_HF_TOKEN", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    return None


class WhisperXTranscriber:
    """使用 whisperX 做对齐，并在要求时做说话人分离。"""

    def __init__(
        self,
        *,
        model: str = "base",
        device: str = "auto",
        compute_type: str = "auto",
        language: str | None = None,
        diarize: bool = False,
        batch_size: int = 16,
    ) -> None:
        self.model_name = model
        self.device_spec = device or "auto"
        self.compute_type_spec = compute_type or "auto"
        self.language = language or None
        self.diarize = bool(diarize)
        self.batch_size = batch_size

    def available(self) -> bool:
        return whisperx_is_installed()

    def transcribe(self, media_path: Path, dest_dir: Path) -> TranscriptResult:
        if not self.available():
            raise DependencyError(WHISPERX_INSTALL_HINT)
        if self.diarize and not hf_token():
            raise DependencyError(DIARIZE_TOKEN_HINT)
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = transcribe_with_whisperx(
                media_path,
                model=self.model_name,
                device=self.device_spec,
                compute_type=self.compute_type_spec,
                language=self.language,
                diarize=self.diarize,
                batch_size=self.batch_size,
            )
        except DependencyError:
            raise
        except JobError:
            raise
        except Exception as exc:
            raise JobError(f"whisperX 转写失败：{type(exc).__name__}: {exc}") from exc
        segments = list(result.get("segments") or [])
        language = result.get("language") or self.language
        diarized = bool(result.get("diarized")) or _has_speakers(segments)
        srt_path = dest_dir / "transcript.srt"
        text_path = dest_dir / "transcript.txt"
        words_path = dest_dir / "transcript.words.json"
        srt_path.write_text(segments_to_srt(segments, with_speakers=diarized), encoding="utf-8")
        if diarized:
            text_path.write_text(segments_to_text(segments, with_speakers=True), encoding="utf-8")
        else:
            write_plain_text(srt_path, text_path)
        words_path.write_text(
            json.dumps(
                {
                    "engine": "whisperx",
                    "model": self.model_name,
                    "language": language,
                    "diarized": diarized,
                    "segments": [segment_to_dict(seg) for seg in segments],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return TranscriptResult(
            srt_path=srt_path,
            text_path=text_path,
            engine="whisperx",
            language=str(language) if language else None,
            model=self.model_name,
            words_path=words_path,
            diarized=diarized,
        )


def transcribe_with_whisperx(
    media_path: Path,
    *,
    model: str,
    device: str,
    compute_type: str,
    language: str | None,
    diarize: bool,
    batch_size: int,
) -> dict[str, Any]:
    """调用 whisperX。测试可整体替换此函数，避免导入真实依赖。"""
    if not whisperx_is_installed():
        raise DependencyError(WHISPERX_INSTALL_HINT)
    import whisperx

    resolved_device = resolve_device(device)
    compute = resolve_compute_type(compute_type, resolved_device)
    try:
        wx_model = whisperx.load_model(model, resolved_device, compute_type=compute)
    except Exception as exc:
        raise JobError(
            "加载 whisperX 模型失败"
            f"（model={model}，device={resolved_device}，compute_type={compute}）："
            f"{type(exc).__name__}: {exc}"
        ) from exc
    audio = _load_audio(whisperx, media_path)
    kwargs: dict[str, Any] = {"batch_size": batch_size}
    if language:
        kwargs["language"] = language
    try:
        result = wx_model.transcribe(audio, **kwargs)
    except Exception as exc:
        raise JobError(f"whisperX 转写失败：{type(exc).__name__}: {exc}") from exc
    if not isinstance(result, dict):
        result = {"segments": list(result or []), "language": language}
    lang = result.get("language") or language
    try:
        model_a, metadata = whisperx.load_align_model(language_code=lang, device=resolved_device)
        aligned = whisperx.align(
            result.get("segments") or [],
            model_a,
            metadata,
            audio,
            resolved_device,
            return_char_alignments=False,
        )
        if isinstance(aligned, dict):
            result = aligned
            result.setdefault("language", lang)
        else:
            result["segments"] = aligned
    except Exception as exc:
        raise JobError(f"whisperX 强制对齐失败：{type(exc).__name__}: {exc}") from exc
    diarized = False
    if diarize:
        token = hf_token()
        if not token:
            raise DependencyError(DIARIZE_TOKEN_HINT)
        pipeline = _diarize_pipeline(whisperx, token, resolved_device)
        try:
            diarize_segments = pipeline(audio)
            result = whisperx.assign_word_speakers(diarize_segments, result)
            diarized = True
        except Exception as exc:
            raise JobError(f"whisperX 说话人分离失败：{type(exc).__name__}: {exc}") from exc
    result["language"] = lang
    result["diarized"] = diarized or _has_speakers(result.get("segments") or [])
    return result


def _load_audio(whisperx: Any, media_path: Path):
    try:
        return whisperx.load_audio(str(media_path))
    except Exception:
        return decode_media_to_pcm(media_path)


def _diarize_pipeline(whisperx: Any, token: str, device: str) -> Any:
    candidates: list[Any] = []
    if hasattr(whisperx, "DiarizationPipeline"):
        candidates.append(whisperx.DiarizationPipeline)
    diarize_mod = getattr(whisperx, "diarize", None)
    if diarize_mod is not None and hasattr(diarize_mod, "DiarizationPipeline"):
        candidates.append(diarize_mod.DiarizationPipeline)
    last_error: Exception | None = None
    for cls in candidates:
        for kwargs in (
            {"use_auth_token": token, "device": device},
            {"token": token, "device": device},
        ):
            try:
                return cls(**kwargs)
            except TypeError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                continue
    detail = f"{type(last_error).__name__}: {last_error}" if last_error else "找不到 DiarizationPipeline"
    raise JobError(f"无法初始化 whisperX 说话人分离：{detail}")


def segments_to_srt(segments: Iterable[object], *, with_speakers: bool = False) -> str:
    lines: list[str] = []
    index = 1
    for segment in segments:
        data = segment_to_dict(segment)
        text = data.get("text") or ""
        if not text:
            continue
        if with_speakers and data.get("speaker"):
            text = f"[{data['speaker']}] {text}"
        start = format_srt_timestamp(data.get("start") or 0)
        end = format_srt_timestamp(data.get("end") or 0)
        if end <= start:
            end = format_srt_timestamp((data.get("start") or 0) + 0.5)
        lines.append(str(index))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
        index += 1
    return "\n".join(lines) + ("\n" if lines else "")


def segments_to_text(segments: Iterable[object], *, with_speakers: bool = False) -> str:
    lines: list[str] = []
    for segment in segments:
        data = segment_to_dict(segment)
        text = data.get("text") or ""
        if not text:
            continue
        if with_speakers and data.get("speaker"):
            lines.append(f"[{data['speaker']}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines) + ("\n" if lines else "")


def segment_to_dict(segment: object) -> dict[str, Any]:
    if isinstance(segment, dict):
        data = dict(segment)
    else:
        data = {
            "start": getattr(segment, "start", 0) or 0,
            "end": getattr(segment, "end", 0) or 0,
            "text": str(getattr(segment, "text", "") or "").strip(),
            "speaker": getattr(segment, "speaker", None),
            "words": getattr(segment, "words", None),
        }
    text = str(data.get("text") or "").strip()
    data["text"] = text
    data["start"] = float(data.get("start") or 0)
    data["end"] = float(data.get("end") or 0)
    speaker = data.get("speaker")
    data["speaker"] = str(speaker) if speaker else _speaker_from_words(data.get("words"))
    words_out: list[dict[str, Any]] = []
    for word in data.get("words") or []:
        if isinstance(word, dict):
            item = {
                "word": str(word.get("word") or word.get("text") or "").strip(),
                "start": word.get("start"),
                "end": word.get("end"),
                "score": word.get("score"),
                "speaker": word.get("speaker"),
            }
        else:
            item = {
                "word": str(getattr(word, "word", getattr(word, "text", "")) or "").strip(),
                "start": getattr(word, "start", None),
                "end": getattr(word, "end", None),
                "score": getattr(word, "score", None),
                "speaker": getattr(word, "speaker", None),
            }
        if item["word"]:
            words_out.append(item)
    data["words"] = words_out
    return data


def _speaker_from_words(words: object) -> str | None:
    for word in words or []:
        speaker = word.get("speaker") if isinstance(word, dict) else getattr(word, "speaker", None)
        if speaker:
            return str(speaker)
    return None


def _has_speakers(segments: Iterable[object]) -> bool:
    for segment in segments:
        if segment_to_dict(segment).get("speaker"):
            return True
    return False
