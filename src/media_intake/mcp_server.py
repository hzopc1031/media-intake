"""media-intake 的 stdio MCP 服务入口。"""

from __future__ import annotations

import sys
from typing import Any

from media_intake import __version__
from media_intake.batches import BatchManager
from media_intake.errors import DependencyError
from media_intake.query import DEFAULT_PAGE_LIMIT

MCP_INSTALL_HINT = (
    "未安装 MCP SDK，无法启动 MCP 服务。这是可选功能，请先安装：\n"
    f"  {sys.executable} -m pip install 'media-intake[mcp]'\n"
    "或：\n"
    f"  {sys.executable} -m pip install mcp"
)

_DEFAULT_MANAGER = BatchManager()


def mcp_is_installed() -> bool:
    try:
        import mcp  # noqa: F401
    except ImportError:
        return False
    return True


def create_server(manager: BatchManager | None = None) -> Any:
    """构造已注册工具的 MCP 服务器。manager 可注入，便于测试。"""
    if not mcp_is_installed():
        raise DependencyError(MCP_INSTALL_HINT)
    from mcp.server import MCPServer

    mgr = manager or _DEFAULT_MANAGER
    server = MCPServer(
        name="media-intake",
        version=__version__,
        instructions=(
            "把获准下载的链接或本地文件排队整理进素材库，并把字幕或转写正文交给模型。"
            "使用 submit_batch 提交一批任务（立即返回 batch_id），"
            "用 get_status 查询批次或单条结果，"
            "用 read_manifest 读取素材库清单，用 read_item 读取条目元数据，"
            "用 read_transcript 按字符分页读取字幕或转写正文，用 search_library 检索素材库，"
            "用 get_metadata 在不下载的情况下探测链接或本地文件。"
            "只应用于你自己的作品，以及平台条款或许可证允许下载的内容。"
        ),
    )

    @server.tool()
    def submit_batch(
        items: list[str],
        library: str,
        format: str | None = None,
        audio_only: bool = False,
        subs: bool = True,
        sub_langs: str | None = None,
        transcribe: str = "never",
        cookies: str | None = None,
        cookies_from_browser: str | None = None,
        expand: bool = False,
        max_items: int | None = None,
        whisper_model: str = "base",
        whisper_device: str = "auto",
        whisper_compute_type: str = "auto",
        whisper_language: str | None = None,
        concurrency: int = 2,
        retries: int = 3,
        force: bool = False,
    ) -> dict[str, Any]:
        """提交一批链接或本地文件，在后台处理并立即返回批次 ID。"""
        return mgr.submit_batch(
            items,
            library,
            format=format,
            audio_only=audio_only,
            subs=subs,
            sub_langs=sub_langs,
            transcribe=transcribe,
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
            expand=expand,
            max_items=max_items,
            whisper_model=whisper_model,
            whisper_device=whisper_device,
            whisper_compute_type=whisper_compute_type,
            whisper_language=whisper_language,
            concurrency=concurrency,
            retries=retries,
            force=force,
        )

    @server.tool()
    def get_status(id: str, library: str | None = None) -> dict[str, Any]:
        """按批次 ID 或条目 ID 查询状态、计数和错误。查询单条条目时需要提供 library。"""
        return mgr.get_status(id, library)

    @server.tool()
    def read_manifest(
        library: str,
        status: str | None = None,
        source_kind: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """读取素材库的 manifest.jsonl。可按 status、source_kind 过滤，并用 limit 限制条数。"""
        return mgr.read_manifest(library, status=status, source_kind=source_kind, limit=limit)

    @server.tool()
    def read_item(library: str, id: str) -> dict[str, Any]:
        """读取已入库条目的元数据，包括标题、时长、路径，以及字幕/转写是否可读和正文长度。"""
        return mgr.read_item(library, id)

    @server.tool()
    def read_transcript(
        library: str,
        id: str,
        kind: str = "auto",
        offset: int = 0,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> dict[str, Any]:
        """把字幕或转写纯文本直接返回给模型。kind 为 auto（优先转写，否则来源字幕）、transcript 或 source。按字符分页，返回 total_length 与 next_offset。"""
        return mgr.read_transcript(library, id, kind=kind, offset=offset, limit=limit)

    @server.tool()
    def search_library(
        library: str,
        query: str,
        limit: int = 20,
        in_text: bool = True,
    ) -> dict[str, Any]:
        """按标题、来源、条目 ID 或正文片段检索素材库，返回匹配记录和可选摘录。"""
        return mgr.search_library(library, query, limit=limit, in_text=in_text)

    @server.tool()
    def get_metadata(
        target: str,
        library: str | None = None,
        cookies: str | None = None,
        cookies_from_browser: str | None = None,
    ) -> dict[str, Any]:
        """读取已入库条目的元数据，或在不下载的情况下探测链接 / 本地文件的标题、时长、是否有字幕。传入 12 位条目 ID 时需要 library。"""
        return mgr.get_metadata(
            target,
            library,
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
        )

    return server


def run_stdio(manager: BatchManager | None = None) -> int:
    server = create_server(manager)
    server.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    # argv 预留与命令行入口一致；stdio 服务不使用位置参数。
    try:
        return run_stdio()
    except DependencyError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
