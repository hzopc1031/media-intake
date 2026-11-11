# 端到端使用演示（0.4.0）

本稿根据 2026-11-11（亚洲/新加坡时间）在一台 Linux 开发机上实际执行的命令整理。命令与输出已经过裁剪，并把本机路径改写成通用写法（`./demo`、`media-intake`、`.venv`）。文中不含 cookie 值、令牌或个人目录。

演示里的本地样例是自建短片和合成语音；在线样例是 Blender 基金会官方开放短片 [Big Buck Bunny](https://www.youtube.com/watch?v=aqz-KE-bpKQ) 及其[官方频道](https://www.youtube.com/channel/UCSMOQeBJ2RAnuFungnQOxLg/videos)上的条目，并用 `--download-sections` 只取片头几秒。导入示例中的小红书链接是占位 URL，文件本身是本地生成的样片，并不是从该平台下载的。请只处理你有权获取的内容。

前提：已安装 Python 3.10+、ffmpeg / ffprobe。仓库尚未发布到 PyPI，以下从源码安装。

## 1. 安装（含 extras）

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev,whisper,mcp]'
media-intake --version
```

实际安装输出（已裁剪依赖列表）：

```text
Obtaining file:///path/to/media-intake
  Installing build dependencies: started
  Installing build dependencies: finished with status 'done'
  Preparing editable metadata (pyproject.toml): started
  Preparing editable metadata (pyproject.toml): finished with status 'done'
Requirement already satisfied: yt-dlp>=2024.8.6 in .venv/lib/python3.13/site-packages (from media-intake==0.4.0) (2026.10.26)
Requirement already satisfied: mcp>=1.0 in .venv/lib/python3.13/site-packages (from media-intake==0.4.0) (2.3.0)
Requirement already satisfied: faster-whisper>=1.0.0 in .venv/lib/python3.13/site-packages (from media-intake==0.4.0) (1.2.1)
Building wheels for collected packages: media-intake
  Building editable for media-intake (pyproject.toml): finished with status 'done'
Successfully built media-intake
Installing collected packages: media-intake
Successfully installed media-intake-0.4.0
```

```text
$ media-intake --version
media-intake 0.4.0
```

可选的 whisperX 后端另装（本演示环境未安装，因此后面只给出切换命令，没有现场转写输出）：

```bash
python3 -m pip install 'media-intake[whisperx]'
```

## 2. 依赖与 cookie 自检

```bash
media-intake doctor
```

```text
依赖自检：
  [ok] ffmpeg: /usr/bin/ffmpeg  (ffmpeg version 7.1.5-0+deb13u1 Copyright (c) 2000-2026 the FFmpeg developers)
  [ok] ffprobe: /usr/bin/ffprobe  (ffprobe version 7.1.5-0+deb13u1 Copyright (c) 2007-2026 the FFmpeg developers)
  [ok] yt-dlp: .venv/bin/yt-dlp  (2026.10.26)
  [ok] faster-whisper  (1.2.1)  （可选）
  [未安装] whisperx  （可选，不影响下载和入库）
      未安装 whisperx，无法使用 whisperX 转写后端。这是可选功能，请先安装：
        python3 -m pip install 'media-intake[whisperx]'
      或：
        python3 -m pip install whisperx
  [ok] mcp  (2.3.0)  （可选）

全部通过。
```

「全部通过」指必备依赖就绪；whisperx 未安装不影响下载、入库和默认的 faster-whisper 转写。

接着用一份**只用于诊断的示例 cookie 文件**检查抖音 / 小红书关键字段。工具只统计名称、域名和过期时间，**不会打印 cookie 值**。下面这份文件里，抖音有尚未过期的 `sessionid`，小红书的 `web_session` 已过期，因此命令以退出码 1 结束——这是预期行为，不是程序故障。

```bash
media-intake cookies check --cookies ./cookies.txt
```

```text
Cookie 自检：
  来源：文件 ./cookies.txt
  读取到 4 条 cookie 记录（只统计名称、域名和过期时间，不会打印值）。

  [ok] 抖音：找到尚未过期的登录相关 cookie，看起来可用；不能保证站点一定放行。
      已找到：sessionid、ttwid
      缺少登录相关：sessionid_ss、sid_tt、sid_guard
      若下载仍失败，多半是页面反爬或安全校验，而不是 cookie 缺失。

  [过期] 小红书：找到登录相关 cookie，但均已过期。
      已找到：a1、web_session
      已过期：web_session（2001-09-09T01:46:40Z）
      建议同时具备：webid
      请重新登录对应网站，再导出 cookies 或改用 --cookies-from-browser。

本工具不会绕过登录墙、安全校验或签名参数。若专用后端或 yt-dlp 仍失败，
可用你信任的平台专用工具取到文件后，再执行 media-intake import 入库。
```

`doctor --cookies ./cookies.txt` 会把上面两段检查合在一次运行里。

## 3. 提交单条链接

以下只下载《Big Buck Bunny》片头 8 秒的 360p 音视频。注意：传给 yt-dlp、且本身以 `-` 开头的参数必须写成 `--ytdlp-option=--foo`，否则会被 argparse 当成另一个选项；时间范围里的 `*` 也请加引号，以免被 shell 展开。

```bash
media-intake run 'https://www.youtube.com/watch?v=aqz-KE-bpKQ' \
  --library ./library-link \
  --format 'bv*[height<=360]+ba/b' \
  --sub-langs en \
  --ytdlp-option=--download-sections \
  --ytdlp-option='*0:00-0:08' \
  --retries 1
```

```text
运行报告
  成功：1    跳过：0    失败：0    取消：0

成功：
  - [ab8cf6bcf09a] Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film
      来源：https://www.youtube.com/watch?v=aqz-KE-bpKQ
      媒体：items/ab8cf6bcf09a/media.mp4
      字幕：无来源字幕
素材库：./library-link
清单：./library-link/manifest.jsonl
索引：./library-link/index.md
```

该片此次探测不到来源字幕。清单里的 `duration_seconds` / 索引「时长」仍是完整影片的 10:35（635 秒），因为这是 yt-dlp 给出的片源时长，不是切片后的文件长度。

## 4. 展开一个小型频道列表

下面把 Blender 官方频道的视频列表展开，最多取 2 条，每条只下载片头 6 秒。频道条目会随时间变化，标题以当时实际展开结果为准。

```bash
media-intake run 'https://www.youtube.com/channel/UCSMOQeBJ2RAnuFungnQOxLg/videos' \
  --expand --max-items 2 \
  --library ./library-pl \
  --format 'bv*[height<=360]+ba/b' \
  --ytdlp-option=--download-sections \
  --ytdlp-option='*0:00-0:06' \
  --retries 1 \
  --no-subs
```

```text
运行报告
  成功：2    跳过：0    失败：0    取消：0

成功：
  - [a35bede29a75] What's New in Blender 5.3! Official Overview
      来源：https://www.youtube.com/watch?v=pvUy69I_Xuk
      媒体：items/a35bede29a75/media.mp4
      字幕：无来源字幕
  - [49006e1dede8] Making of: The Blender Open Movies Exhibition
      来源：https://www.youtube.com/watch?v=mHrHLqQj7Ts
      媒体：items/49006e1dede8/media.mp4
      字幕：无来源字幕
素材库：./library-pl
清单：./library-pl/manifest.jsonl
索引：./library-pl/index.md
```

对应的 `index.md`（工具写入的「更新时间」为 UTC）：

```markdown
# 素材索引

更新时间（UTC）：2026-11-11T04:49:15Z

共 2 条。

| ID | 标题 | 来源 | 时长 | 媒体 | 字幕 | 转写 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 49006e1dede8 | Making of: The Blender Open Movies Exhibition | https://www.youtube.com/watch?v=mHrHLqQj7Ts | 4:39 | items/49006e1dede8/media.mp4 |  |  | 成功 |
| a35bede29a75 | What's New in Blender 5.3! Official Overview | https://www.youtube.com/watch?v=pvUy69I_Xuk | 28:01 | items/a35bede29a75/media.mp4 |  |  | 成功 |
```

## 5. 入队、启动 worker、查询与取消

`media-intake run` 会在当前进程里立刻处理；`submit` 默认只写入素材库的 SQLite 队列。客户端退出后，需要另开一个 `worker`（别名 `serve`）来领取任务。

```bash
media-intake submit ./talk.mp4 ./speech.wav --library ./library-queue --retries 0
```

```text
已入队：batch-a134c2e6970d4c2da5d1d18fe6302c11
状态：queued
条目：2
素材库：./library-queue
请运行：media-intake worker --library ./library-queue
```

```bash
media-intake status batch-a134c2e6970d4c2da5d1d18fe6302c11 --library ./library-queue
media-intake batches --library ./library-queue
```

```text
批次 batch-a134c2e6970d4c2da5d1d18fe6302c11
状态：queued
素材库：./library-queue
条目：2
计数：succeeded 0，skipped 0，failed 0，cancelled 0，total 2
```

```text
素材库 ./library-queue 共 1 个批次：

批次 batch-a134c2e6970d4c2da5d1d18fe6302c11
状态：queued
素材库：./library-queue
条目：2
计数：succeeded 0，skipped 0，failed 0，cancelled 0，total 2
```

用 `--once` 领取并处理当前队列后退出（长期运行则去掉该选项）：

```bash
media-intake worker --library ./library-queue --once
```

```text
worker 正在检查队列：./library-queue
```

处理完成后再次查询：

```text
$ media-intake status batch-a134c2e6970d4c2da5d1d18fe6302c11 --library ./library-queue
批次 batch-a134c2e6970d4c2da5d1d18fe6302c11
状态：succeeded
素材库：./library-queue
条目：2
计数：succeeded 2，skipped 0，failed 0，cancelled 0，total 2
任务：
  - [19fbcd572ecb] succeeded talk
  - [9036d0d77d14] succeeded speech
```

取消尚未开始的批次（本例在 queued 时取消成功）：

```bash
media-intake submit ./talk.mp4 --library ./library-queue --force --retries 0
media-intake cancel batch-c07360c5dbd743e69082b6e03ef9a623 --library ./library-queue
```

```text
已入队：batch-c07360c5dbd743e69082b6e03ef9a623
状态：queued
条目：1
素材库：./library-queue
请运行：media-intake worker --library ./library-queue
```

```text
批次 batch-c07360c5dbd743e69082b6e03ef9a623
状态：cancelled
素材库：./library-queue
条目：1
计数：succeeded 0，skipped 0，failed 0，cancelled 0，total 1
已请求取消。
错误：已取消
```

若批次已在运行，`cancel` 会设置取消标志；worker 在下一条任务开始前或重试等待期间检查该标志并退出。心跳过期的运行中批次会被后续 worker 回收。

## 6. 来源字幕与 faster-whisper 转写（以及如何改用 whisperX）

带外挂字幕的本地短片会直接归档来源字幕，不必启动转写：

```bash
media-intake run ./talk.mp4 --library ./library-local --retries 0
```

```text
运行报告
  成功：1    跳过：0    失败：0    取消：0

成功：
  - [19fbcd572ecb] talk
      来源：./talk.mp4
      媒体：items/19fbcd572ecb/media.mp4
      字幕：items/19fbcd572ecb/source.srt（source）
素材库：./library-local
清单：./library-local/manifest.jsonl
索引：./library-local/index.md
```

`items/19fbcd572ecb/source.txt` 内容为：

```text
这是一条来源字幕，用于对照离线转写。
```

没有来源字幕的音频，使用默认的 faster-whisper（本例强制英文、tiny 模型、CPU）：

```bash
media-intake run ./speech.wav --library ./library-asr \
  --transcribe always --whisper-model tiny \
  --whisper-device cpu --whisper-language en --retries 0
```

```text
运行报告
  成功：1    跳过：0    失败：0    取消：0

成功：
  - [9036d0d77d14] speech
      来源：./speech.wav
      媒体：items/9036d0d77d14/media.wav
      字幕：无来源字幕
      转写：items/9036d0d77d14/transcript.srt（模型 tiny，语言 en）
素材库：./library-asr
清单：./library-asr/manifest.jsonl
索引：./library-asr/index.md
```

`items/9036d0d77d14/transcript.txt` 内容为：

```text
The water is blue today.
```

清单中可见转写引擎：

```json
{
  "id": "9036d0d77d14",
  "title": "speech",
  "transcript_path": "items/9036d0d77d14/transcript.srt",
  "transcript_text_path": "items/9036d0d77d14/transcript.txt",
  "transcript_model": "tiny",
  "transcript_language": "en",
  "transcript_engine": "faster-whisper",
  "transcript_words_path": null,
  "transcript_diarized": false,
  "status": "succeeded"
}
```

`--transcribe missing`（本节稍后 MCP 示例用到）表示：已有来源字幕就用字幕，没有再转写。

要改用 whisperX（词级时间戳，可选说话人分离），先安装 extra，再指定后端。说话人分离所需的 Hugging Face 令牌只从环境变量读取（`MEDIA_INTAKE_HF_TOKEN`、`HF_TOKEN` 或 `HUGGING_FACE_HUB_TOKEN`），不要写进命令行、配置文件或仓库。本环境未安装 whisperX，下列命令未实际执行：

```bash
python3 -m pip install 'media-intake[whisperx]'
# 在当前 shell 里设置令牌，不要把它写进历史记录或仓库
media-intake run ./speech.wav --library ./library-asr \
  --transcribe always --asr-backend whisperx --diarize \
  --whisper-model tiny --retries 0
```

成功后除 `transcript.srt` / `transcript.txt` 外，还会写出 `transcript.words.json`；开启 `--diarize` 时，SRT/TXT 中会带说话人标签，清单字段 `transcript_engine` 为 `whisperx`，`transcript_diarized` 为 `true`。

## 7. 导入外部工具已下载的文件

当页面有登录墙或专用客户端才能取到文件时，把已经到手的媒体交给 `import`，并补上来源链接和标题等元数据。来源 URL 会写入清单，以便日后按同一链接跳过重复项。

```bash
media-intake import ./external-clip.mp4 \
  --library ./library-import \
  --source-url https://www.xiaohongshu.com/explore/demo123 \
  --title 外部工具下载的示例笔记 \
  --uploader 示例作者 \
  --platform xiaohongshu \
  --upload-date 20261111 \
  --retries 0
```

```text
运行报告
  成功：1    跳过：0    失败：0    取消：0

成功：
  - [2bb34c51b6fe] 外部工具下载的示例笔记
      来源：https://www.xiaohongshu.com/explore/demo123
      媒体：items/2bb34c51b6fe/media.mp4
      字幕：无来源字幕
素材库：./library-import
清单：./library-import/manifest.jsonl
索引：./library-import/index.md
```

## 8. 浏览素材库（manifest / index.md）

每个素材库根目录都有一行一条的 `manifest.jsonl` 和根据清单生成的 `index.md`。导入示例的清单行（已是完整原文，仅把本地路径改成 `./demo`）：

```json
{"id": "2bb34c51b6fe", "source": "https://www.xiaohongshu.com/explore/demo123", "source_kind": "url", "title": "外部工具下载的示例笔记", "duration_seconds": 2.0, "item_dir": "items/2bb34c51b6fe", "media_path": "items/2bb34c51b6fe/media.mp4", "subtitle_path": null, "subtitle_text_path": null, "subtitle_kind": null, "transcript_path": null, "transcript_text_path": null, "transcript_model": null, "transcript_language": null, "transcript_engine": null, "transcript_words_path": null, "transcript_diarized": null, "parent_source": null, "platform": "xiaohongshu", "uploader": "示例作者", "upload_date": "20261111", "thumbnail_path": null, "status": "succeeded", "error": null, "attempts": 0}
```

```markdown
# 素材索引

更新时间（UTC）：2026-11-11T04:47:22Z

共 1 条。

| ID | 标题 | 来源 | 时长 | 媒体 | 字幕 | 转写 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2bb34c51b6fe | 外部工具下载的示例笔记 | https://www.xiaohongshu.com/explore/demo123 | 0:02 | items/2bb34c51b6fe/media.mp4 |  |  | 成功 |
```

单条链接入库后的索引（时长为完整片源时长，见第 3 节说明）：

```markdown
# 素材索引

更新时间（UTC）：2026-11-11T04:48:09Z

共 1 条。

| ID | 标题 | 来源 | 时长 | 媒体 | 字幕 | 转写 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ab8cf6bcf09a | Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film | https://www.youtube.com/watch?v=aqz-KE-bpKQ | 10:35 | items/ab8cf6bcf09a/media.mp4 |  |  | 成功 |
```

## 9. 从 Agent 使用 MCP 服务

默认的 `submit_batch` 只入队（`detach=true`），需要另开 worker 才会执行。进程内执行可在工具参数里传 `detach=false`。

### 客户端配置示例

Claude Desktop（`claude_desktop_config.json`）或 Cursor（`mcp.json`）都可以用类似片段：

```json
{
  "mcpServers": {
    "media-intake": {
      "command": "media-intake",
      "args": ["mcp"]
    }
  }
}
```

若命令不在 PATH 里，把 `command` 改成虚拟环境中的绝对路径，例如 `/path/to/.venv/bin/media-intake`。

当前工具：`submit_batch`、`get_status`、`list_batches`、`cancel_batch`、`read_manifest`、`read_item`、`read_transcript`、`search_library`、`get_metadata`。

### 为演示准备一座同时带字幕和转写的素材库

```bash
media-intake run ./talk.mp4 ./speech.wav --library ./library-mcp \
  --transcribe missing --whisper-model tiny \
  --whisper-device cpu --whisper-language en --retries 0
```

```text
运行报告
  成功：2    跳过：0    失败：0    取消：0

成功：
  - [19fbcd572ecb] talk
      来源：./talk.mp4
      媒体：items/19fbcd572ecb/media.mp4
      字幕：items/19fbcd572ecb/source.srt（source）
  - [9036d0d77d14] speech
      来源：./speech.wav
      媒体：items/9036d0d77d14/media.wav
      字幕：无来源字幕
      转写：items/9036d0d77d14/transcript.srt（模型 tiny，语言 en）
素材库：./library-mcp
清单：./library-mcp/manifest.jsonl
索引：./library-mcp/index.md
```

### 小型 MCP 客户端

下面的脚本通过官方 `mcp` SDK 以进程内方式连接同一套工具实现（与 stdio 服务暴露的工具一致），并打印结构化结果。实际演示执行的是：

```bash
python3 mcp_client_demo.py ./library-mcp ./talk.mp4
```

```python
#!/usr/bin/env python3
import asyncio
import json
import sys
from typing import Any

from mcp import Client
from media_intake.mcp_server import create_server


def payload(result: Any) -> Any:
    if getattr(result, "is_error", False):
        text = ""
        content = getattr(result, "content", None) or []
        if content:
            text = getattr(content[0], "text", str(content[0]))
        return {"error": True, "message": text or str(result)}
    if getattr(result, "structured_content", None) is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def show(title: str, data: Any) -> None:
    print(f"## {title}")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print()


async def main() -> None:
    library, media = sys.argv[1], sys.argv[2]
    async with Client(create_server()) as client:
        tools = await client.list_tools()
        show("list_tools", sorted(tool.name for tool in tools.tools))

        manifest = payload(await client.call_tool("read_manifest", {"library": library}))
        show("read_manifest", manifest)
        speech_id = next(r["id"] for r in manifest["records"] if r.get("transcript_text_path"))
        talk_id = next(r["id"] for r in manifest["records"] if r.get("subtitle_text_path"))

        show("read_item", payload(await client.call_tool("read_item", {"library": library, "id": speech_id})))
        show(
            "read_transcript (speech)",
            payload(await client.call_tool("read_transcript", {
                "library": library, "id": speech_id, "kind": "auto", "limit": 200,
            })),
        )
        show(
            "read_transcript (source subs)",
            payload(await client.call_tool("read_transcript", {
                "library": library, "id": talk_id, "kind": "source", "limit": 200,
            })),
        )
        show("search_library", payload(await client.call_tool("search_library", {
            "library": library, "query": "water is blue",
        })))
        show("get_metadata", payload(await client.call_tool("get_metadata", {
            "target": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
        })))
        submitted = payload(await client.call_tool("submit_batch", {
            "items": [media], "library": library, "transcribe": "never", "detach": True,
        }))
        show("submit_batch", submitted)
        show("list_batches", payload(await client.call_tool("list_batches", {"library": library})))
        show("cancel_batch", payload(await client.call_tool("cancel_batch", {
            "id": submitted["batch_id"], "library": library,
        })))


if __name__ == "__main__":
    asyncio.run(main())
```

### 真实工具输出（已裁剪并改写路径）

`list_tools`：

```json
[
  "cancel_batch",
  "get_metadata",
  "get_status",
  "list_batches",
  "read_item",
  "read_manifest",
  "read_transcript",
  "search_library",
  "submit_batch"
]
```

`read_item`（转写条目 `9036d0d77d14`）：

```json
{
  "kind": "item",
  "library": "./library-mcp",
  "id": "9036d0d77d14",
  "source": "./speech.wav",
  "source_kind": "local",
  "title": "speech",
  "duration_seconds": 1.695,
  "status": "succeeded",
  "item_dir": "items/9036d0d77d14",
  "media_path": "items/9036d0d77d14/media.wav",
  "transcript_path": "items/9036d0d77d14/transcript.srt",
  "transcript_text_path": "items/9036d0d77d14/transcript.txt",
  "transcript_model": "tiny",
  "transcript_language": "en",
  "transcript_engine": "faster-whisper",
  "transcript_diarized": false,
  "has_source_text": false,
  "has_transcript": true,
  "has_words": false,
  "source_text_length": 0,
  "transcript_text_length": 25
}
```

`read_transcript` 按字符分页，并返回 `total_length` 与 `next_offset`（本例全文短于 `limit`，故 `next_offset` 为 `null`）：

```json
{
  "kind": "transcript",
  "library": "./library-mcp",
  "id": "9036d0d77d14",
  "title": "speech",
  "text_kind": "transcript",
  "language": "en",
  "model": "tiny",
  "engine": "faster-whisper",
  "text": "The water is blue today.\n",
  "offset": 0,
  "limit": 200,
  "returned_length": 25,
  "total_length": 25,
  "next_offset": null
}
```

同一座库里带源字幕的条目：

```json
{
  "kind": "transcript",
  "library": "./library-mcp",
  "id": "19fbcd572ecb",
  "title": "talk",
  "text_kind": "source",
  "language": null,
  "model": null,
  "engine": null,
  "text": "这是一条来源字幕，用于对照离线转写。\n",
  "offset": 0,
  "limit": 200,
  "returned_length": 19,
  "total_length": 19,
  "next_offset": null
}
```

`search_library`：

```json
{
  "library": "./library-mcp",
  "query": "water is blue",
  "count": 1,
  "records": [
    {
      "id": "9036d0d77d14",
      "title": "speech",
      "source": "./speech.wav",
      "source_kind": "local",
      "status": "succeeded",
      "matched_in": ["transcript"],
      "snippet": "The water is blue today."
    }
  ]
}
```

`get_metadata` 在不下载的情况下探测链接：

```json
{
  "kind": "probe",
  "source": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
  "source_kind": "url",
  "title": "Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film",
  "duration_seconds": 635.0,
  "has_subtitles": false,
  "has_automatic_captions": false,
  "is_live": false,
  "platform": "Youtube",
  "uploader": "Blender",
  "upload_date": "20141110"
}
```

默认入队、列出（此处只保留刚刚提交的那一条）并取消：

```json
{
  "batch_id": "batch-3a16bd4c917945b184d60a1d52082d60",
  "status": "queued",
  "item_count": 1,
  "library": "./library-mcp",
  "detach": true
}
```

```json
{
  "library": "./library-mcp",
  "count": 1,
  "records": [
    {
      "kind": "batch",
      "batch_id": "batch-3a16bd4c917945b184d60a1d52082d60",
      "status": "queued",
      "library": "./library-mcp",
      "item_count": 1,
      "counts": {
        "succeeded": 0,
        "skipped": 0,
        "failed": 0,
        "cancelled": 0,
        "total": 1
      },
      "jobs": [],
      "error": null,
      "exit_code": null,
      "cancel_requested": false,
      "created_at": "2026-11-11T04:49:58Z",
      "updated_at": "2026-11-11T04:49:58Z"
    }
  ]
}
```

```json
{
  "kind": "batch",
  "batch_id": "batch-3a16bd4c917945b184d60a1d52082d60",
  "status": "cancelled",
  "library": "./library-mcp",
  "item_count": 1,
  "counts": {
    "succeeded": 0,
    "skipped": 0,
    "failed": 0,
    "cancelled": 0,
    "total": 1
  },
  "jobs": [],
  "error": "已取消",
  "exit_code": 1,
  "cancel_requested": true,
  "created_at": "2026-11-11T04:49:58Z",
  "updated_at": "2026-11-11T04:49:58Z"
}
```

时间戳为 UTC。对应的亚洲/新加坡时间是 2026-11-11 12:49:58。

## 说明与限制

- 抖音 / 小红书等站点仍可能因登录墙或安全校验失败；本工具只做 cookie 诊断，不会绕过反爬，也不提供代理轮换。下载失败时可用外部工具取文件，再 `import`。
- MCP 默认只入队，需要同时运行 `media-intake worker --library <素材库>`。
- whisperX 是可选、较重的依赖；说话人分离需要 Hugging Face 令牌。
- 使用 `--download-sections` 时，清单时长可能仍是完整片源时长。
- `--ytdlp-option` 后接以 `-` 开头的参数时必须使用等号写法。
- 仓库尚未发布到 PyPI。Windows 为尽力支持。
