from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from media_intake.errors import DependencyError
from media_intake.process import CommandRunner, SubprocessRunner

_INSTALL_HINTS = {
    "ffmpeg": (
        "未找到 ffmpeg。请先安装后再运行：\n"
        "  macOS:  brew install ffmpeg\n"
        "  Debian/Ubuntu:  sudo apt-get update && sudo apt-get install -y ffmpeg\n"
        "  Windows:  使用官网构建，或 winget install FFmpeg，并确保已加入 PATH。"
    ),
    "ffprobe": (
        "未找到 ffprobe（通常随 ffmpeg 一起安装）。请安装 ffmpeg 并确保 ffprobe 在 PATH 中。"
    ),
    "yt-dlp": (
        "未找到 yt-dlp。推荐用 pip 安装到当前 Python 环境：\n"
        f"  {sys.executable} -m pip install -U yt-dlp\n"
        "也可按 yt-dlp 官方说明安装独立可执行文件，并加入 PATH。"
    ),
}


@dataclass(frozen=True)
class ToolInfo:
    name: str
    path: Path | None
    version: str | None
    ok: bool
    detail: str


def find_executable(name: str) -> Path | None:
    found = shutil.which(name)
    return Path(found) if found else None


def _version_from(runner: CommandRunner, path: Path, *args: str) -> str | None:
    result = runner.run([str(path), *args], timeout=30)
    if result.returncode != 0:
        return None
    text = (result.stdout or result.stderr).strip()
    return text.splitlines()[0].strip() if text else None


def inspect_tool(name: str, version_args: tuple[str, ...], runner: CommandRunner | None = None) -> ToolInfo:
    runner = runner or SubprocessRunner()
    path = find_executable(name)
    if path is None:
        return ToolInfo(name=name, path=None, version=None, ok=False, detail=_INSTALL_HINTS[name])
    try:
        version = _version_from(runner, path, *version_args)
    except OSError as exc:
        return ToolInfo(
            name=name,
            path=path,
            version=None,
            ok=False,
            detail=f"找到 {path}，但无法执行：{exc}",
        )
    if not version:
        return ToolInfo(
            name=name,
            path=path,
            version=None,
            ok=False,
            detail=f"找到 {path}，但查询版本失败，请确认它是可执行的 {name}。",
        )
    return ToolInfo(name=name, path=path, version=version, ok=True, detail=str(path))


def inspect_ffmpeg(runner: CommandRunner | None = None) -> ToolInfo:
    return inspect_tool("ffmpeg", ("-version",), runner)


def inspect_ffprobe(runner: CommandRunner | None = None) -> ToolInfo:
    return inspect_tool("ffprobe", ("-version",), runner)


def inspect_ytdlp(runner: CommandRunner | None = None) -> ToolInfo:
    return inspect_tool("yt-dlp", ("--version",), runner)


def require_ffmpeg(runner: CommandRunner | None = None) -> tuple[Path, Path]:
    ffmpeg = inspect_ffmpeg(runner)
    ffprobe = inspect_ffprobe(runner)
    missing = [info for info in (ffmpeg, ffprobe) if not info.ok]
    if missing:
        raise DependencyError("\n\n".join(info.detail for info in missing))
    assert ffmpeg.path and ffprobe.path
    return ffmpeg.path, ffprobe.path


def require_ytdlp(runner: CommandRunner | None = None) -> Path:
    info = inspect_ytdlp(runner)
    if not info.ok or info.path is None:
        raise DependencyError(info.detail)
    return info.path


def format_doctor_report(
    tools: list[ToolInfo],
    *,
    optional: list[ToolInfo] | None = None,
) -> str:
    lines = ["依赖自检："]
    all_ok = True
    for info in tools:
        if info.ok:
            lines.append(f"  [ok] {info.name}: {info.path}  ({info.version})")
        else:
            all_ok = False
            lines.append(f"  [缺失] {info.name}")
            for row in info.detail.splitlines():
                lines.append(f"      {row}")
    for info in optional or []:
        if info.ok:
            version = f"  ({info.version})" if info.version else ""
            where = f": {info.path}" if info.path else ""
            lines.append(f"  [ok] {info.name}{where}{version}  （可选）")
        else:
            lines.append(f"  [未安装] {info.name}  （可选，不影响下载和入库）")
            for row in info.detail.splitlines():
                lines.append(f"      {row}")
    lines.append("")
    lines.append("全部通过。" if all_ok else "存在缺失或不可执行的依赖，请按上面的提示安装后再试。")
    return "\n".join(lines) + "\n"


def inspect_faster_whisper() -> ToolInfo:
    from media_intake.transcribe import WHISPER_INSTALL_HINT, whisper_is_installed

    if not whisper_is_installed():
        return ToolInfo(
            name="faster-whisper",
            path=None,
            version=None,
            ok=False,
            detail=WHISPER_INSTALL_HINT,
        )
    import faster_whisper

    version = getattr(faster_whisper, "__version__", None)
    return ToolInfo(
        name="faster-whisper",
        path=None,
        version=version or "installed",
        ok=True,
        detail="optional",
    )


def doctor_text(runner: CommandRunner | None = None) -> tuple[str, int]:
    tools = [
        inspect_ffmpeg(runner),
        inspect_ffprobe(runner),
        inspect_ytdlp(runner),
    ]
    text = format_doctor_report(tools, optional=[inspect_faster_whisper()])
    return text, (0 if all(t.ok for t in tools) else 1)


def update_ytdlp(runner: CommandRunner | None = None) -> tuple[str, int]:
    """用已有安装方式升级 yt-dlp，工具本身不下载未校验的二进制。"""
    runner = runner or SubprocessRunner()
    lines: list[str] = []
    # 优先升级当前解释器里的 pip 包，这样与「pip install media-intake」一致。
    pip_cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]
    pip = runner.run(pip_cmd, timeout=300)
    lines.append("$ " + " ".join(pip_cmd))
    if pip.stdout.strip():
        lines.append(pip.stdout.strip())
    if pip.stderr.strip():
        lines.append(pip.stderr.strip())
    if pip.returncode == 0:
        info = inspect_ytdlp(runner)
        if info.ok:
            lines.append(f"当前 yt-dlp：{info.path}  ({info.version})")
        return "\n".join(lines) + "\n", 0

    # pip 失败时，若 PATH 上有独立可执行文件，再尝试官方的 -U。
    binary = find_executable("yt-dlp")
    if binary is None:
        lines.append(_INSTALL_HINTS["yt-dlp"])
        return "\n".join(lines) + "\n", 1
    up = runner.run([str(binary), "-U"], timeout=300)
    lines.append(f"$ {binary} -U")
    if up.stdout.strip():
        lines.append(up.stdout.strip())
    if up.stderr.strip():
        lines.append(up.stderr.strip())
    return "\n".join(lines) + "\n", up.returncode

