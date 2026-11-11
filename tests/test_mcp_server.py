from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import generate_sample_media, requires_ffmpeg


def mcp_installed() -> bool:
    try:
        import mcp  # noqa: F401
    except ImportError:
        return False
    return True


requires_mcp = pytest.mark.skipif(not mcp_installed(), reason="未安装 mcp")


def _payload(result: Any) -> dict[str, Any]:
    if getattr(result, "is_error", False):
        text = ""
        content = getattr(result, "content", None) or []
        if content:
            text = getattr(content[0], "text", str(content[0]))
        raise AssertionError(f"工具调用失败：{text or result}")
    if getattr(result, "structured_content", None) is not None:
        data = result.structured_content
        return data if isinstance(data, dict) else json.loads(data)
    text = result.content[0].text
    return json.loads(text)


async def _wait_status(client: Any, batch_id: str, library: Path, *, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _payload(await client.call_tool("get_status", {"id": batch_id, "library": str(library)}))
        if last["status"] in {"succeeded", "completed", "failed"}:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"批次未在时限内结束：{last}")


@requires_mcp
def test_create_server_requires_sdk(monkeypatch) -> None:
    import media_intake.mcp_server as mcp_server
    from media_intake.errors import DependencyError

    monkeypatch.setattr(mcp_server, "mcp_is_installed", lambda: False)
    with pytest.raises(DependencyError, match="media-intake\\[mcp\\]"):
        mcp_server.create_server()


@requires_mcp
def test_tool_handlers_validate_inputs(tmp_path: Path) -> None:
    from mcp import Client

    from media_intake.batches import BatchManager
    from media_intake.mcp_server import create_server
    from media_intake.models import JobResult, JobStatus, RunReport, SourceKind

    def run(items, options, **kwargs):
        job = JobResult(
            id="010203040506",
            source=items[0],
            source_kind=SourceKind.LOCAL,
            status=JobStatus.SUCCEEDED,
            title="ok",
        )
        return RunReport(succeeded=[job])

    server = create_server(BatchManager(run_batch_fn=run))

    async def go() -> None:
        async with Client(server) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            expected = {
                "submit_batch",
                "get_status",
                "read_manifest",
                "read_item",
                "read_transcript",
                "search_library",
                "get_metadata",
            }
            assert names == expected
            empty = await client.call_tool(
                "submit_batch",
                {"items": [], "library": str(tmp_path / "lib")},
            )
            assert empty.is_error
            submitted = _payload(
                await client.call_tool(
                    "submit_batch",
                    {"items": ["clip.mp4"], "library": str(tmp_path / "lib")},
                )
            )
            assert submitted["batch_id"].startswith("batch-")
            status = await _wait_status(client, submitted["batch_id"], tmp_path / "lib", timeout=5)
            assert status["kind"] == "batch"
            assert status["status"] == "succeeded"
            assert status["counts"]["succeeded"] == 1
            missing = await client.call_tool(
                "submit_batch",
                {
                    "items": ["clip.mp4"],
                    "library": str(tmp_path / "lib"),
                    "cookies": str(tmp_path / "no-such-cookies.txt"),
                },
            )
            assert missing.is_error

    asyncio.run(go())


@requires_mcp
@requires_ffmpeg
def test_inprocess_roundtrip_local_media(tmp_path: Path) -> None:
    from mcp import Client

    from media_intake.mcp_server import create_server

    media = generate_sample_media(tmp_path / "clip.mp4")
    sidecar = media.with_suffix(".srt")
    sidecar.write_text("1\n00:00:00,000 --> 00:00:01,000\n本地测试字幕。\n", encoding="utf-8")
    library = tmp_path / "library"
    server = create_server()

    async def go() -> None:
        async with Client(server) as client:
            submitted = _payload(
                await client.call_tool(
                    "submit_batch",
                    {"items": [str(media)], "library": str(library), "transcribe": "never"},
                )
            )
            assert submitted["batch_id"].startswith("batch-")
            assert submitted["item_count"] == 1
            status = await _wait_status(client, submitted["batch_id"], library)
            assert status["status"] == "succeeded"
            assert status["counts"]["succeeded"] == 1
            assert status["jobs"]
            job_id = status["jobs"][0]["id"]
            item = _payload(await client.call_tool("get_status", {"id": job_id, "library": str(library)}))
            assert item["kind"] == "item"
            assert item["status"] == "succeeded"
            manifest = _payload(await client.call_tool("read_manifest", {"library": str(library), "limit": 10}))
            assert manifest["count"] >= 1
            assert manifest["records"][0]["status"] == "succeeded"
            assert manifest["records"][0]["source_kind"] == "local"
            only_local = _payload(
                await client.call_tool(
                    "read_manifest",
                    {"library": str(library), "source_kind": "local"},
                )
            )
            assert only_local["count"] == manifest["count"]
            item_meta = _payload(
                await client.call_tool("read_item", {"library": str(library), "id": job_id})
            )
            assert item_meta["has_source_text"] is True
            assert item_meta["title"]
            body = _payload(
                await client.call_tool(
                    "read_transcript",
                    {"library": str(library), "id": job_id, "kind": "source", "limit": 200},
                )
            )
            assert "本地测试字幕" in body["text"]
            assert body["total_length"] >= body["returned_length"]
            assert body["next_offset"] is None
            found = _payload(
                await client.call_tool(
                    "search_library",
                    {"library": str(library), "query": "本地测试字幕"},
                )
            )
            assert found["count"] >= 1
            assert found["records"][0]["id"] == job_id

    asyncio.run(go())
