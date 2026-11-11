from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from media_intake import __version__
from media_intake.config import BatchOptions
from media_intake.deps import doctor_text, update_ytdlp
from media_intake.errors import IntakeError
from media_intake.inputs import parse_lines, read_input_file
from media_intake.pipeline import run_batch
from media_intake.report import format_report

LEGAL_NOTE = "本工具用于下载你自己的作品，以及平台条款或许可证允许下载的内容。"

DEFAULT_LIBRARY = "media-library"


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return _dispatch(args)
    except IntakeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 130


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="media-intake",
        description=(
            "把公开或获准下载的链接、以及本地媒体文件，排队入库并整理字幕与素材清单。\n\n"
            + LEGAL_NOTE
        ),
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser(
        "run",
        help="处理一批链接或本地文件",
        description=(
            "从命令行、文本文件或标准输入读取条目（每行一个链接或本地路径），"
            "排队下载或归档，并把结果写入素材库。\n\n"
            + LEGAL_NOTE
        ),
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run_p.add_argument("items", nargs="*", help="单条链接或本地路径，可给多项")
    run_p.add_argument(
        "-i",
        "--input",
        dest="input_file",
        metavar="FILE",
        help="文本文件，每行一个链接或本地路径；使用 - 表示从标准输入读取",
    )
    run_p.add_argument(
        "-L",
        "--library",
        default=DEFAULT_LIBRARY,
        help=f"素材库目录（默认：{DEFAULT_LIBRARY}）",
    )
    run_p.add_argument(
        "-j",
        "--concurrency",
        type=int,
        default=2,
        help="并发数，必须大于等于 1（默认：2）",
    )
    run_p.add_argument(
        "--retries",
        type=int,
        default=3,
        help="失败后的重试次数（默认：3，即最多尝试 4 次）",
    )
    run_p.add_argument(
        "--retry-wait",
        type=float,
        default=1.0,
        help="首次重试前等待的秒数，之后按指数退避（默认：1）",
    )
    run_p.add_argument(
        "-f",
        "--format",
        dest="format",
        help="传给 yt-dlp 的格式选择器，例如 bv*+ba/b 或 best[height<=720]",
    )
    run_p.add_argument(
        "-x",
        "--audio-only",
        action="store_true",
        help="只提取音频（yt-dlp -x，默认封装为 m4a）",
    )
    run_p.add_argument(
        "--subs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否尝试下载来源字幕（默认开启；--no-subs 关闭）",
    )
    run_p.add_argument(
        "--sub-langs",
        help="字幕语言，逗号分隔，例如 zh,en,zh-Hans；默认由 yt-dlp 决定",
    )
    run_p.add_argument(
        "--write-auto-subs",
        action="store_true",
        help="在来源字幕之外，同时请求自动生成字幕（若站点提供）",
    )
    run_p.add_argument(
        "--cookies",
        metavar="FILE",
        help="Netscape 格式的 cookies 文件，传给 yt-dlp --cookies（不会把文件内容写入日志）",
    )
    run_p.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER[:PROFILE]",
        help="从本机浏览器读取 cookies，传给 yt-dlp --cookies-from-browser，例如 chrome 或 chrome:Default",
    )
    run_p.add_argument(
        "--expand",
        "--playlist",
        dest="expand",
        action="store_true",
        help="展开播放列表或创作者主页，把其中每个视频作为独立队列条目（默认只处理单条，不展开）",
    )
    run_p.add_argument(
        "--max-items",
        type=int,
        default=None,
        metavar="N",
        help="与 --expand 一起使用，每个输入链接最多展开多少条",
    )
    run_p.add_argument(
        "--ytdlp-option",
        action="append",
        default=[],
        metavar="OPT",
        help="额外传给 yt-dlp 的单个参数，可重复。可用于登录相关选项，例如 --ytdlp-option --username",
    )
    run_p.add_argument(
        "--transcribe",
        nargs="?",
        const="missing",
        default="never",
        choices=["never", "missing", "always"],
        metavar="MODE",
        help=(
            "离线转写模式：never 不转写（默认）；"
            "missing 仅在没有来源字幕时转写；"
            "always 即使已有来源字幕也转写。"
            "单独写 --transcribe 等于 missing。"
            "需要先安装 media-intake[whisper]"
        ),
    )
    run_p.add_argument(
        "--whisper-model",
        default="base",
        metavar="NAME_OR_PATH",
        help="faster-whisper 模型名（tiny、base、small、medium、large-v3 等）或本地模型目录（默认：base）",
    )
    run_p.add_argument(
        "--whisper-device",
        default="auto",
        metavar="DEVICE",
        help="转写设备：auto、cpu 或 cuda（默认：auto）",
    )
    run_p.add_argument(
        "--whisper-compute-type",
        default="auto",
        metavar="TYPE",
        help="计算类型：auto、int8、float16、float32 等（默认 auto：CPU 用 int8，CUDA 用 float16）",
    )
    run_p.add_argument(
        "--whisper-language",
        default="auto",
        metavar="LANG",
        help="转写语言，例如 en、zh；默认 auto，由模型自动检测",
    )
    run_p.add_argument(
        "--force",
        action="store_true",
        help="重新处理素材库中已经成功的条目",
    )
    run_p.add_argument(
        "--reference",
        action="store_true",
        help="本地文件优先用硬链接或符号链接入库，不行再复制",
    )

    sub.add_parser(
        "doctor",
        help="检查 ffmpeg、ffprobe、yt-dlp，以及可选的 faster-whisper 和 MCP SDK",
        description="检查本机依赖（ffmpeg、ffprobe、yt-dlp；faster-whisper 与 MCP SDK 为可选项），并在缺失时给出安装提示。\n\n" + LEGAL_NOTE,
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_parser(
        "mcp",
        help="以 stdio 方式启动 MCP 服务",
        description=(
            "启动最小的 stdio MCP 服务器，提供 submit_batch、get_status、read_manifest 三个工具。"
            "需要先安装 media-intake[mcp]。请由 MCP 客户端拉起本命令，不要在终端里交互使用。\n\n"
            + LEGAL_NOTE
        ),
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_parser(
        "update-yt-dlp",
        help="升级当前环境中的 yt-dlp",
        description=(
            "使用 pip 升级当前 Python 环境中的 yt-dlp；"
            "若失败且 PATH 中有独立可执行文件，再尝试 yt-dlp -U。"
            "本工具不会自行下载未校验的二进制文件。\n\n"
            + LEGAL_NOTE
        ),
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        text, code = doctor_text()
        print(text, end="")
        return code
    if args.command == "update-yt-dlp":
        text, code = update_ytdlp()
        print(text, end="")
        return code
    if args.command == "mcp":
        from media_intake.mcp_server import run_stdio

        return run_stdio()
    if args.command == "run":
        return _cmd_run(args)
    raise AssertionError(f"未知命令：{args.command}")


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        items = _collect_items(args)
        options = BatchOptions(
            library_root=Path(args.library),
            concurrency=args.concurrency,
            retries=args.retries,
            retry_wait=args.retry_wait,
            format=args.format,
            audio_only=args.audio_only,
            write_subs=args.subs,
            sub_langs=args.sub_langs,
            write_auto_subs=args.write_auto_subs,
            cookies_file=Path(args.cookies) if args.cookies else None,
            cookies_from_browser=args.cookies_from_browser,
            extra_ytdlp_args=list(args.ytdlp_option or []),
            force=args.force,
            reference_local=args.reference,
            expand=args.expand,
            max_items=args.max_items,
            transcribe_mode=args.transcribe,
            whisper_model=args.whisper_model,
            whisper_device=args.whisper_device,
            whisper_compute_type=args.whisper_compute_type,
            whisper_language=args.whisper_language,
        )
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    if options.cookies_file is not None and not options.cookies_file.is_file():
        print(f"错误：找不到 cookies 文件：{options.cookies_file}", file=sys.stderr)
        return 2
    report = run_batch(items, options)
    print(format_report(report), end="")
    print(f"素材库：{options.library_root}")
    print(f"清单：{options.library_root / 'manifest.jsonl'}")
    print(f"索引：{options.library_root / 'index.md'}")
    return report.exit_code


def _collect_items(args: argparse.Namespace) -> list[str]:
    items: list[str] = list(args.items or [])
    if args.input_file:
        if args.input_file == "-":
            items.extend(parse_lines(sys.stdin.read().splitlines()))
        else:
            path = Path(args.input_file)
            if not path.is_file():
                raise IntakeError(f"找不到输入文件：{path}")
            items.extend(read_input_file(path))
    elif not items and not sys.stdin.isatty():
        items.extend(parse_lines(sys.stdin.read().splitlines()))
    if not items:
        raise IntakeError("请在命令行给出条目，或使用 --input 指定文件（- 表示标准输入）")
    return items
