from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from media_intake import __version__
from media_intake.batches import BatchManager
from media_intake.config import BatchOptions
from media_intake.cookies_check import inspect_cookies
from media_intake.deps import doctor_text, update_ytdlp
from media_intake.errors import IntakeError
from media_intake.importer import import_local_media
from media_intake.inputs import parse_lines, read_input_file
from media_intake.pipeline import run_batch
from media_intake.report import format_report
from media_intake.settings import KNOWN_KEYS, load_settings

LEGAL_NOTE = "本工具用于下载你自己的作品，以及平台条款或许可证允许下载的内容。"

DEFAULT_LIBRARY = "media-library"


def main(argv: Sequence[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else None
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config")
    pre_args, _ = pre.parse_known_args(argv_list)
    settings = load_settings(pre_args.config)
    parser = _build_parser()
    _apply_settings(parser, settings)
    args = parser.parse_args(argv_list)
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
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="读取 TOML 配置文件；默认还会合并 ~/.config/media-intake/config.toml 与当前目录的 media-intake.toml。命令行选项优先",
    )
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
    _add_input_arguments(run_p)
    _add_batch_arguments(run_p)

    submit_p = sub.add_parser(
        "submit",
        help="把一批任务写入队列，交由 worker 在后台处理",
        description=(
            "与 run 接受相同的条目和选项，但只把批次写入素材库队列并立即返回 batch_id；"
            "不会在当前进程里执行下载或转写。"
            "请另外运行 media-intake worker --library <目录> 领取任务。"
            "客户端退出后，已入队的批次仍可由 worker 继续处理。\n\n"
            + LEGAL_NOTE
        ),
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_input_arguments(submit_p)
    _add_batch_arguments(submit_p)

    worker_p = sub.add_parser(
        "worker",
        aliases=["serve"],
        help="领取并处理素材库中已入队的批次",
        description=(
            "独立运行的后台进程：从素材库的 SQLite 队列领取批次并执行。"
            "MCP 客户端或 media-intake submit 退出后，未完成的批次仍会继续。"
            "若领取到心跳过期的运行中批次，会当作崩溃回收并重新执行"
            "（已成功的条目默认仍会跳过）。\n\n"
            + LEGAL_NOTE
        ),
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    worker_p.add_argument(
        "-L",
        "--library",
        default=DEFAULT_LIBRARY,
        help=f"素材库目录（默认：{DEFAULT_LIBRARY}）",
    )
    worker_p.add_argument(
        "--once",
        action="store_true",
        help="最多处理一条批次后退出；若队列为空则立即退出",
    )
    worker_p.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        metavar="SEC",
        help="队列为空时的轮询间隔秒数（默认：1）",
    )
    worker_p.add_argument(
        "--stale-after",
        type=float,
        default=120.0,
        metavar="SEC",
        help="运行中批次的心跳超过该秒数后可被其他 worker 回收（默认：120）",
    )

    status_p = sub.add_parser(
        "status",
        help="查询批次或条目状态；省略 ID 时列出批次",
        description="查询一个批次或条目的状态；若不给 ID，则列出该素材库中的批次。\n\n" + LEGAL_NOTE,
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    status_p.add_argument("id", nargs="?", help="批次 ID 或条目 ID")
    status_p.add_argument(
        "-L",
        "--library",
        default=DEFAULT_LIBRARY,
        help=f"素材库目录（默认：{DEFAULT_LIBRARY}）",
    )

    cancel_p = sub.add_parser(
        "cancel",
        help="取消一个尚未结束的批次",
        description=(
            "取消排队中或运行中的批次。已排队的会立即标记为 cancelled；"
            "运行中的会尽快停止尚未开始的条目，正在处理的条目会尽力收尾。\n\n"
            + LEGAL_NOTE
        ),
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cancel_p.add_argument("id", help="批次 ID")
    cancel_p.add_argument(
        "-L",
        "--library",
        default=DEFAULT_LIBRARY,
        help=f"素材库目录（默认：{DEFAULT_LIBRARY}）",
    )

    batches_p = sub.add_parser(
        "batches",
        help="列出素材库中的批次",
        description="列出素材库队列里的批次及其状态。\n\n" + LEGAL_NOTE,
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    batches_p.add_argument(
        "-L",
        "--library",
        default=DEFAULT_LIBRARY,
        help=f"素材库目录（默认：{DEFAULT_LIBRARY}）",
    )
    batches_p.add_argument("--status", help="按状态过滤，例如 queued、running、succeeded、cancelled")
    batches_p.add_argument("--limit", type=int, default=None, help="最多列出多少条")

    doctor_p = sub.add_parser(
        "doctor",
        help="检查 ffmpeg、ffprobe、yt-dlp，以及可选转写依赖、MCP SDK 和 cookies",
        description=(
            "检查本机依赖（ffmpeg、ffprobe、yt-dlp；faster-whisper、whisperx 与 MCP SDK 为可选项），"
            "并在缺失时给出安装提示。可选检查抖音 / 小红书 cookies 是否存在、过期或缺少登录字段，不会打印 cookie 值。\n\n"
            + LEGAL_NOTE
        ),
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_cookie_arguments(doctor_p)

    cookies_p = sub.add_parser(
        "cookies",
        help="检查抖音 / 小红书 cookies 是否可用（不打印值）",
        description=(
            "检查 Netscape cookies 文件或本机浏览器 cookies 中，抖音与小红书的关键字段是否存在或过期。"
            "只报告名称、域名和过期时间，不会打印 cookie 值。"
            "本工具不会绕过登录墙或反爬校验。\n\n"
            + LEGAL_NOTE
        ),
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cookies_sub = cookies_p.add_subparsers(dest="cookies_command", required=True)
    cookies_check = cookies_sub.add_parser(
        "check",
        help="检查 cookies 文件或浏览器 cookies",
        description=cookies_p.description,
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_cookie_arguments(cookies_check)

    import_p = sub.add_parser(
        "import",
        help="把外部工具已下载的本地文件导入素材库",
        description=(
            "将本地音视频或图片归档进素材库，并可附上来源链接、标题、作者等元数据。"
            "适合先用你信任的抖音 / 小红书专用工具取到文件，再交给本工具做入库和转写。"
            "不会访问来源网站，也不会绕过反爬。\n\n"
            + LEGAL_NOTE
        ),
        epilog=LEGAL_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    import_p.add_argument("file", help="要导入的本地媒体文件")
    import_p.add_argument("--source-url", help="原始页面链接，写入清单并用于稳定去重")
    import_p.add_argument("--title", help="覆盖标题")
    import_p.add_argument("--uploader", help="作者或账号名")
    import_p.add_argument("--platform", help="平台名，例如 douyin、xiaohongshu")
    import_p.add_argument("--upload-date", help="发布日期，建议 YYYYMMDD")
    _add_batch_arguments(import_p)
    sub.add_parser(
        "mcp",
        help="以 stdio 方式启动 MCP 服务",
        description=(
            "启动 stdio MCP 服务器，提供 submit_batch、get_status、read_manifest、"
            "read_item、read_transcript、search_library、get_metadata、"
            "cancel_batch、list_batches 等工具。"
            "submit_batch 默认只入队，需另开 media-intake worker 处理；"
            "也可传入 detach=false 在当前进程的后台线程执行（客户端退出后未完成批次会停）。"
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


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("items", nargs="*", help="单条链接或本地路径，可给多项")
    parser.add_argument(
        "-i",
        "--input",
        dest="input_file",
        metavar="FILE",
        help="文本文件，每行一个链接或本地路径；使用 - 表示从标准输入读取",
    )


def _add_batch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-L",
        "--library",
        default=DEFAULT_LIBRARY,
        help=f"素材库目录（默认：{DEFAULT_LIBRARY}）",
    )
    parser.add_argument(
        "-j",
        "--concurrency",
        type=int,
        default=2,
        help="并发数，必须大于等于 1（默认：2）",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="失败后的重试次数（默认：3，即最多尝试 4 次）",
    )
    parser.add_argument(
        "--retry-wait",
        type=float,
        default=1.0,
        help="首次重试前等待的秒数，之后按指数退避（默认：1）",
    )
    parser.add_argument(
        "-f",
        "--format",
        dest="format",
        help="传给 yt-dlp 的格式选择器，例如 bv*+ba/b 或 best[height<=720]",
    )
    parser.add_argument(
        "-x",
        "--audio-only",
        action="store_true",
        help="只提取音频（yt-dlp -x，默认封装为 m4a）",
    )
    parser.add_argument(
        "--subs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否尝试下载来源字幕（默认开启；--no-subs 关闭）",
    )
    parser.add_argument(
        "--sub-langs",
        help="字幕语言，逗号分隔，例如 zh,en,zh-Hans。默认相当于 all，并排除 B 站弹幕 danmaku（否则 --convert-subs srt 可能把整次下载判失败）",
    )
    parser.add_argument(
        "--write-auto-subs",
        action="store_true",
        help="在来源字幕之外，同时请求自动生成字幕（若站点提供）",
    )
    parser.add_argument(
        "--cookies",
        metavar="FILE",
        help="Netscape 格式的 cookies 文件，传给 yt-dlp --cookies（不会把文件内容写入日志）",
    )
    parser.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER[:PROFILE]",
        help="从本机浏览器读取 cookies，传给 yt-dlp --cookies-from-browser，例如 chrome 或 chrome:Default",
    )
    parser.add_argument(
        "--expand",
        "--playlist",
        dest="expand",
        action="store_true",
        help="展开播放列表或创作者主页，把其中每个视频作为独立队列条目（默认只处理单条，不展开）",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        metavar="N",
        help="与 --expand 一起使用，每个输入链接最多展开多少条",
    )
    parser.add_argument(
        "--ytdlp-option",
        action="append",
        default=[],
        metavar="OPT",
        help="额外传给 yt-dlp 的单个参数，可重复。若参数本身以 - 开头，请写成 --ytdlp-option=--foo，例如 --ytdlp-option=--username",
    )
    parser.add_argument(
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
            "需要先安装 media-intake[whisper] 或 [whisperx]"
        ),
    )
    parser.add_argument(
        "--asr-backend",
        default="faster-whisper",
        choices=["faster-whisper", "whisperx"],
        help="转写后端：faster-whisper（默认，轻量）或 whisperx（词级时间戳，可选说话人分离）",
    )
    parser.add_argument(
        "--diarize",
        action="store_true",
        help="在 whisperX 后端上启用说话人分离（需要环境变量中的 Hugging Face 令牌，不会写入仓库）",
    )
    parser.add_argument(
        "--whisper-model",
        default="base",
        metavar="NAME_OR_PATH",
        help="转写模型名（tiny、base、small、medium、large-v3 等）或本地模型目录（默认：base）",
    )
    parser.add_argument(
        "--whisper-device",
        default="auto",
        metavar="DEVICE",
        help="转写设备：auto、cpu 或 cuda（默认：auto）",
    )
    parser.add_argument(
        "--whisper-compute-type",
        default="auto",
        metavar="TYPE",
        help="计算类型：auto、int8、float16、float32 等（默认 auto：CPU 用 int8，CUDA 用 float16）",
    )
    parser.add_argument(
        "--whisper-language",
        default="auto",
        metavar="LANG",
        help="转写语言，例如 en、zh；默认 auto，由模型自动检测",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="重新处理素材库中已经成功的条目",
    )
    parser.add_argument(
        "--reference",
        action="store_true",
        help="本地文件优先用硬链接或符号链接入库，不行再复制",
    )


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        text, code = doctor_text()
        print(text, end="")
        cookie_args = _cookie_args(args)
        if cookie_args[0] is not None or cookie_args[1] is not None:
            cookie_text, _reports, cookie_code = inspect_cookies(
                cookies_file=cookie_args[0],
                cookies_from_browser=cookie_args[1],
            )
            print(cookie_text, end="")
            if cookie_code != 0 and code == 0:
                code = cookie_code
        return code
    if args.command == "cookies":
        return _cmd_cookies(args)
    if args.command == "import":
        return _cmd_import(args)
    if args.command == "update-yt-dlp":
        text, code = update_ytdlp()
        print(text, end="")
        return code
    if args.command == "mcp":
        from media_intake.mcp_server import run_stdio

        return run_stdio()
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "submit":
        return _cmd_submit(args)
    if args.command in {"worker", "serve"}:
        return _cmd_worker(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "cancel":
        return _cmd_cancel(args)
    if args.command == "batches":
        return _cmd_batches(args)
    raise AssertionError(f"未知命令：{args.command}")


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        items = _collect_items(args)
        options = _options_from_args(args)
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


def _cmd_submit(args: argparse.Namespace) -> int:
    try:
        items = _collect_items(args)
        options = _options_from_args(args)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    if options.cookies_file is not None and not options.cookies_file.is_file():
        print(f"错误：找不到 cookies 文件：{options.cookies_file}", file=sys.stderr)
        return 2
    manager = BatchManager(detach=True)
    submitted = manager.submit_batch(items, str(options.library_root), **_submit_kwargs(args), detach=True)
    print(f"已入队：{submitted['batch_id']}")
    print(f"状态：{submitted['status']}")
    print(f"条目：{submitted['item_count']}")
    print(f"素材库：{submitted['library']}")
    print(f"请运行：media-intake worker --library {submitted['library']}")
    return 0


def _cmd_worker(args: argparse.Namespace) -> int:
    from media_intake.worker import run_worker

    if args.poll_interval < 0 or args.stale_after < 0:
        print("错误：--poll-interval 与 --stale-after 不能为负数", file=sys.stderr)
        return 2
    library = Path(args.library).expanduser().resolve()
    if args.once:
        print(f"worker 正在检查队列：{library}", file=sys.stderr)
    else:
        print(f"worker 已启动，正在监听：{library}", file=sys.stderr)
    return run_worker(
        library,
        once=args.once,
        poll_interval=args.poll_interval,
        stale_after=args.stale_after,
    )


def _cmd_status(args: argparse.Namespace) -> int:
    manager = BatchManager()
    if not args.id:
        result = manager.list_batches(args.library)
        print(_format_batch_list(result), end="")
        return 0
    result = manager.get_status(args.id, args.library)
    print(_format_status(result), end="")
    return 0


def _cmd_cancel(args: argparse.Namespace) -> int:
    manager = BatchManager()
    result = manager.cancel_batch(args.id, args.library)
    print(_format_status(result), end="")
    return 0


def _cmd_batches(args: argparse.Namespace) -> int:
    manager = BatchManager()
    result = manager.list_batches(args.library, status=args.status, limit=args.limit)
    print(_format_batch_list(result), end="")
    return 0


def _options_from_args(args: argparse.Namespace) -> BatchOptions:
    return BatchOptions(
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
        asr_backend=args.asr_backend,
        diarize=args.diarize,
    )


def _submit_kwargs(args: argparse.Namespace) -> dict:
    return {
        "format": args.format,
        "audio_only": args.audio_only,
        "subs": args.subs,
        "sub_langs": args.sub_langs,
        "write_auto_subs": args.write_auto_subs,
        "transcribe": args.transcribe,
        "cookies": args.cookies,
        "cookies_from_browser": args.cookies_from_browser,
        "extra_ytdlp_args": list(args.ytdlp_option or []),
        "expand": args.expand,
        "max_items": args.max_items,
        "whisper_model": args.whisper_model,
        "whisper_device": args.whisper_device,
        "whisper_compute_type": args.whisper_compute_type,
        "whisper_language": args.whisper_language,
        "asr_backend": args.asr_backend,
        "diarize": args.diarize,
        "concurrency": args.concurrency,
        "retries": args.retries,
        "retry_wait": args.retry_wait,
        "force": args.force,
        "reference_local": args.reference,
    }


def _format_status(result: dict) -> str:
    if result.get("kind") == "item":
        lines = [
            f"条目 {result.get('id')}",
            f"状态：{result.get('status')}",
            f"标题：{result.get('title') or '（无标题）'}",
            f"来源：{result.get('source')}",
        ]
        if result.get("error"):
            lines.append(f"错误：{result['error']}")
        if result.get("media_path"):
            lines.append(f"媒体：{result['media_path']}")
        lines.append("")
        return "\n".join(lines)
    return _format_one_batch(result) + "\n"


def _format_one_batch(result: dict) -> str:
    lines = [
        f"批次 {result.get('batch_id')}",
        f"状态：{result.get('status')}",
        f"素材库：{result.get('library')}",
        f"条目：{result.get('item_count')}",
    ]
    counts = result.get("counts") or {}
    if counts:
        parts = [f"{key} {counts[key]}" for key in ("succeeded", "skipped", "failed", "cancelled", "total") if key in counts]
        if parts:
            lines.append("计数：" + "，".join(parts))
    if result.get("cancel_requested"):
        lines.append("已请求取消。")
    if result.get("error"):
        lines.append(f"错误：{result['error']}")
    jobs = result.get("jobs") or []
    if jobs:
        lines.append("任务：")
        for job in jobs:
            err = f"：{job['error']}" if job.get("error") else ""
            lines.append(f"  - [{job.get('id')}] {job.get('status')} {job.get('title') or job.get('source')}{err}")
    return "\n".join(lines)


def _format_batch_list(result: dict) -> str:
    records = result.get("records") or []
    if not records:
        return f"素材库 {result.get('library')} 中没有批次。\n"
    lines = [f"素材库 {result.get('library')} 共 {result.get('count')} 个批次：", ""]
    for rec in records:
        lines.append(_format_one_batch(rec))
        lines.append("")
    return "\n".join(lines)




def _add_cookie_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cookies",
        metavar="FILE",
        help="Netscape 格式的 cookies 文件（不会打印文件内容）",
    )
    parser.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER[:PROFILE]",
        help="从本机浏览器读取 cookies 并只检查名称与过期时间，例如 chrome 或 chrome:Default",
    )


def _cookie_args(args: argparse.Namespace) -> tuple[Path | None, str | None]:
    path = Path(args.cookies).expanduser() if getattr(args, "cookies", None) else None
    browser = getattr(args, "cookies_from_browser", None)
    return path, browser


def _cmd_cookies(args: argparse.Namespace) -> int:
    path, browser = _cookie_args(args)
    if path is None and not browser:
        print("错误：请提供 --cookies 或 --cookies-from-browser", file=sys.stderr)
        return 2
    text, _reports, code = inspect_cookies(cookies_file=path, cookies_from_browser=browser)
    print(text, end="")
    return code


def _cmd_import(args: argparse.Namespace) -> int:
    try:
        options = _options_from_args(args)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    if options.cookies_file is not None and not options.cookies_file.is_file():
        print(f"错误：找不到 cookies 文件：{options.cookies_file}", file=sys.stderr)
        return 2
    report = import_local_media(
        Path(args.file),
        options,
        source_url=args.source_url,
        title=args.title,
        uploader=args.uploader,
        platform=args.platform,
        upload_date=args.upload_date,
    )
    print(format_report(report), end="")
    print(f"素材库：{options.library_root}")
    print(f"清单：{options.library_root / 'manifest.jsonl'}")
    print(f"索引：{options.library_root / 'index.md'}")
    return report.exit_code


def _apply_settings(parser: argparse.ArgumentParser, settings: dict) -> None:
    clean = {key: value for key, value in settings.items() if key in KNOWN_KEYS}
    if not clean or parser._subparsers is None:
        return
    for action in parser._subparsers._group_actions:
        for sub in action.choices.values():
            applicable = {
                key: value
                for key, value in clean.items()
                if any(getattr(opt, "dest", None) == key for opt in sub._actions)
            }
            if applicable:
                sub.set_defaults(**applicable)


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
