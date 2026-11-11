# 端到端使用演示（0.4.1）

本稿主要依据 2026-11-11（亚洲/新加坡时间，UTC+8）在一台**中国大陆 Linux 云服务器**上的实测整理。安装方式按真实用户路径：从 GitHub 上的 `main` 安装当时的 0.4.0，再补上本次发现并已并入 0.4.1 的修复。命令与输出已经过裁剪，路径改写成通用写法（`~/media-intake-trial`、`media-intake`、`.venv`），不含 cookie 值、令牌、主机地址或个人账号。

在线样例是 Blender 基金会开放授权动画在 B 站上的转载合集，以及《大雄兔》（Big Buck Bunny）；只用 `--download-sections` 取了片头几秒，并限制在 360p。导入示例中的小红书链接是占位 URL，文件本身是本地生成的样片。本地语音由本机 ffmpeg/flite 合成。请只处理你有权获取的内容。

部署细节（无 root 时的 ffmpeg、PyPI / GitHub / Hugging Face 镜像、B 站 412 等）见 [docs/server-deploy.md](server-deploy.md)。

## 1. 安装（含 extras）

云主机上已有 Python 3.12 和 git / curl，但没有系统级 ffmpeg，也没有 sudo。ffmpeg 6.1.1 是从发行版仓库下载 deb 后解压到 `~/media-intake-trial/opt`，再用包装脚本调用的；官方静态包站点可访问但只有约 20 KB/s，因而没有采用。

```bash
python3 -m venv ~/media-intake-trial/.venv
source ~/media-intake-trial/.venv/bin/activate
export PATH="$HOME/media-intake-trial/bin:$HOME/media-intake-trial/.venv/bin:$PATH"
export PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
export PIP_TRUSTED_HOST=mirrors.cloud.tencent.com
```

先按文档尝试官方 GitHub 安装：

```bash
python -m pip install "media-intake[whisper,mcp] @ git+https://github.com/hzopc1031/media-intake.git@main"
```

实测卡在 `git clone https://github.com/hzopc1031/media-intake.git`，45 秒后被中止（退出码 124）。`github.com` 本身也连接超时。改用当时可通的 `codeload.github.com` 获取 `main` 源码包，依赖仍走 PyPI 镜像：

```bash
curl -L -o src/media-intake-main.tar.gz \
  https://codeload.github.com/hzopc1031/media-intake/tar.gz/refs/heads/main
python -m pip install "src/media-intake-main.tar.gz[whisper,mcp]"
media-intake --version
```

安装输出（已裁剪依赖列表）：

```text
Looking in indexes: https://mirrors.cloud.tencent.com/pypi/simple
Collecting media-intake==0.4.0
...
Successfully built media-intake
Successfully installed ... faster-whisper-1.2.1 mcp-2.3.0 media-intake-0.4.0 yt-dlp-2026.10.26 ...
```

```text
real 19.16
$ media-intake --version
media-intake 0.4.0
```

可选的 whisperX 另装。本次在同一台 4 核、约 8 GB 内存的 CPU 主机上执行 `pip install 'media-intake[whisperx]'`，pip 开始拉取带 CUDA 的 PyTorch / transformers / pyannote 等巨型依赖，3 分钟后仍未结束，已中止。安装过程还会占用数 GB 磁盘，且可能留下不完整的 GPU 轮子。试用请以 faster-whisper 为准；whisperX 需要单独环境和更多时间，见 [server-deploy.md](server-deploy.md)。

## 2. 依赖与 cookie 自检

```bash
media-intake doctor
```

```text
依赖自检：
  [ok] ffmpeg: ~/media-intake-trial/bin/ffmpeg  (ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 the FFmpeg developers)
  [ok] ffprobe: ~/media-intake-trial/bin/ffprobe  (ffprobe version 6.1.1-3ubuntu5 Copyright (c) 2007-2023 the FFmpeg developers)
  [ok] yt-dlp: ~/media-intake-trial/.venv/bin/yt-dlp  (2026.10.26)
  [ok] faster-whisper  (1.2.1)  （可选）
  [未安装] whisperx  （可选，不影响下载和入库）
      未安装 whisperx，无法使用 whisperX 转写后端。这是可选功能，请先安装：
        python3 -m pip install 'media-intake[whisperx]'
      或：
        python3 -m pip install whisperx
  [ok] mcp  (2.3.0)  （可选）

全部通过。
```

接着用不含任何记录的 Netscape 示例文件检查抖音 / 小红书关键字段。工具只统计名称、域名和过期时间，**不会打印 cookie 值**。没有登录相关 cookie 时以退出码 1 结束，这是预期行为：

```bash
media-intake cookies check --cookies ./empty-cookies.txt
```

```text
Cookie 自检：
  来源：文件 ./empty-cookies.txt
  读取到 0 条 cookie 记录（只统计名称、域名和过期时间，不会打印值）。

  [缺少] 抖音：没有找到该站点域名下的 cookie。
      缺少登录相关：sessionid、sessionid_ss、sid_tt、sid_guard
      建议同时具备：ttwid、odin_tt、passport_csrf_token
      请在本机浏览器登录后，使用 --cookies 提供 Netscape 格式文件，或使用 --cookies-from-browser。不要把 cookie 文件提交到 git。

  [缺少] 小红书：没有找到该站点域名下的 cookie。
      缺少登录相关：web_session
      建议同时具备：a1、webid
      请在本机浏览器登录后，使用 --cookies 提供 Netscape 格式文件，或使用 --cookies-from-browser。不要把 cookie 文件提交到 git。

本工具不会绕过登录墙、安全校验或签名参数。若专用后端或 yt-dlp 仍失败，
可用你信任的平台专用工具取到文件后，再执行 media-intake import 入库。
```

未提供 `--cookies` / `--cookies-from-browser` 时，命令会提示需要其中之一。

## 3. 提交单条公开链接

本环境访问 YouTube 失败（`Network is unreachable`，25 秒超时），因此改用 B 站上的开放动画《大雄兔》。在该云主机上，yt-dlp 默认的浏览器式 User-Agent 会遇到 HTTP 412；加上 `--ytdlp-option=--user-agent --ytdlp-option=yt-dlp` 后可以解析。只取片头 8 秒、360p。

0.4.0 在默认开启字幕转换时，会把 B 站弹幕 `danmaku` 交给 `--convert-subs srt`，从而把整次下载判失败。显式排除弹幕后成功（0.4.1 已把这一排除做成默认行为）：

```bash
media-intake run 'https://www.bilibili.com/video/BV1DT4y1z7a1?p=2' \
  --library ./library-link \
  --format 'bv*[height<=360]+ba/b[height<=360]/worst' \
  --sub-langs 'all,-danmaku' \
  --ytdlp-option=--user-agent --ytdlp-option=yt-dlp \
  --ytdlp-option=--download-sections --ytdlp-option='*0:00-0:08' \
  --retries 1
```

```text
运行报告
  成功：1    跳过：0    失败：0    取消：0

成功：
  - [82685d1b16c2] 【4K】《大雄兔(Big Buck Bunny)》（2008）动画短片 p02 1080P-30fps
      来源：https://www.bilibili.com/video/BV1DT4y1z7a1?p=2
      媒体：items/82685d1b16c2/media.mp4
      字幕：无来源字幕
素材库：./library-link
清单：./library-link/manifest.jsonl
索引：./library-link/index.md
```

这次没有可下载的对白字幕轨道（该页只提供弹幕）。清单 / 索引中的时长仍是完整片源的 10:35，不是 8 秒切片。命令本身约 2 秒完成。

对比：同一环境下，0.4.0 默认参数（`--write-subs --convert-subs srt`，不排除 danmaku）会得到：

```text
失败：
  - [...] (无标题)
      原因：yt-dlp 下载失败（... --write-subs --convert-subs srt ...）：
      ERROR: Preprocessing: Error opening input files: Invalid data found when processing input
```

## 4. 展开一个小型分 P 列表

下面把 B 站上的「Blender 开源动画电影合集」（20 P）展开，最多取 2 条，每条只下载片头 6 秒。这里加了 `--no-subs`，因此即使用 0.4.0 也不会踩到弹幕转换问题。

```bash
media-intake run 'https://www.bilibili.com/video/BV1VY411d7mc' \
  --expand --max-items 2 \
  --library ./library-pl \
  --format 'bv*[height<=360]+ba/b[height<=360]/worst' \
  --ytdlp-option=--user-agent --ytdlp-option=yt-dlp \
  --ytdlp-option=--download-sections --ytdlp-option='*0:00-0:06' \
  --retries 1 --no-subs
```

```text
运行报告
  成功：2    跳过：0    失败：0    取消：0

成功：
  - [86fb36decd7c] Blender开源动画电影合集 p01 SINGULARITY - Painterly Space Adventure（奇点 - 绘画太空冒险） 2026
      来源：https://www.bilibili.com/video/BV1VY411d7mc?p=1
      媒体：items/86fb36decd7c/media.mp4
      字幕：无来源字幕
  - [695433ed3a74] Blender开源动画电影合集 p02 WING IT（天马行空） 2023
      来源：https://www.bilibili.com/video/BV1VY411d7mc?p=2
      媒体：items/695433ed3a74/media.mp4
      字幕：无来源字幕
素材库：./library-pl
清单：./library-pl/manifest.jsonl
索引：./library-pl/index.md
```

约 3.3 秒。对应的 `index.md`（工具写入的「更新时间」为 UTC；下面这条等于 2026-11-11 14:06:39，亚洲/新加坡时间）：

```markdown
# 素材索引

更新时间（UTC）：2026-11-11T06:06:39Z

共 2 条。

| ID | 标题 | 来源 | 时长 | 媒体 | 字幕 | 转写 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 695433ed3a74 | Blender开源动画电影合集 p02 WING IT（天马行空） 2023 | https://www.bilibili.com/video/BV1VY411d7mc?p=2 | 3:58 | items/695433ed3a74/media.mp4 |  |  | 成功 |
| 86fb36decd7c | Blender开源动画电影合集 p01 SINGULARITY - Painterly Space Adventure（奇点 - 绘画太空冒险） 2026 | https://www.bilibili.com/video/BV1VY411d7mc?p=1 | 6:31 | items/86fb36decd7c/media.mp4 |  |  | 成功 |
```

## 5. 入队、启动 worker、查询与取消

```bash
media-intake submit ./talk.mp4 ./speech.wav --library ./library-queue --retries 0
```

```text
已入队：batch-5e59d25cca1541f88b0f4b8ca3eb7a07
状态：queued
条目：2
素材库：./library-queue
请运行：media-intake worker --library ./library-queue
```

```bash
media-intake status batch-5e59d25cca1541f88b0f4b8ca3eb7a07 --library ./library-queue
```

```text
批次 batch-5e59d25cca1541f88b0f4b8ca3eb7a07
状态：queued
素材库：./library-queue
条目：2
计数：succeeded 0，skipped 0，failed 0，cancelled 0，total 2
```

```bash
media-intake worker --library ./library-queue --once
```

```text
worker 正在检查队列：./library-queue
```

处理后再次查询（worker 本身约 0.4 秒）：

```text
批次 batch-5e59d25cca1541f88b0f4b8ca3eb7a07
状态：succeeded
素材库：./library-queue
条目：2
计数：succeeded 2，skipped 0，failed 0，cancelled 0，total 2
任务：
  - [606d2ed035b7] succeeded talk
  - [c0bde6dfe568] succeeded speech
```

取消尚未开始的批次：

```bash
media-intake submit ./talk.mp4 --library ./library-queue --force --retries 0
media-intake cancel batch-4ba82355efbf427eadeaaf5dfab7c92c --library ./library-queue
```

```text
已入队：batch-4ba82355efbf427eadeaaf5dfab7c92c
状态：queued
```

```text
批次 batch-4ba82355efbf427eadeaaf5dfab7c92c
状态：cancelled
素材库：./library-queue
条目：1
计数：succeeded 0，skipped 0，failed 0，cancelled 0，total 1
已请求取消。
错误：已取消
```

## 6. 来源字幕与 faster-whisper 转写

带外挂字幕的本地短片会直接归档来源字幕：

```bash
media-intake run ./talk.mp4 --library ./library-local --retries 0
```

```text
运行报告
  成功：1    跳过：0    失败：0    取消：0

成功：
  - [606d2ed035b7] talk
      来源：./talk.mp4
      媒体：items/606d2ed035b7/media.mp4
      字幕：items/606d2ed035b7/source.srt（source）
素材库：./library-local
清单：./library-local/manifest.jsonl
索引：./library-local/index.md
```

`source.txt` 内容为：

```text
这是一条来源字幕，用于对照离线转写。
```

没有来源字幕的合成语音，使用默认 faster-whisper（tiny、CPU、英语）。请先设置 Hugging Face 镜像，否则在中国大陆可能下载不到模型：

```bash
export HF_ENDPOINT=https://hf-mirror.com
export HF_HOME="$HOME/media-intake-trial/.hf"
media-intake run ./speech.wav --library ./library-asr \
  --transcribe always --whisper-model tiny \
  --whisper-device cpu --whisper-language en --retries 0
```

```text
运行报告
  成功：1    跳过：0    失败：0    取消：0

成功：
  - [c0bde6dfe568] speech
      来源：./speech.wav
      媒体：items/c0bde6dfe568/media.wav
      字幕：无来源字幕
      转写：items/c0bde6dfe568/transcript.srt（模型 tiny，语言 en）
素材库：./library-asr
清单：./library-asr/manifest.jsonl
索引：./library-asr/index.md
```

首次含下载 tiny 模型约 45 秒；`transcript.txt` 为：

```text
The water is blue today.
```

清单中的引擎字段为 `faster-whisper`。`--transcribe missing` 表示已有来源字幕就用字幕，没有再转写。

改用 whisperX 的命令与此前相同（先安装 extra，再 `--asr-backend whisperx`，可选 `--diarize`）。本次试用环境没有实际跑通 whisperX，见第 1 节。令牌只从环境变量读取，不要写进命令行或仓库。

本次未能从公开 B 站链接拿到可下载的对白字幕（开放电影转载页只有弹幕；一些「官方双语」视频是烧进画面的字幕）。来源字幕路径用本地侧车文件验证。

## 7. 导入外部工具已下载的文件

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

导入示例的清单行（已把本地路径改成通用写法）：

```json
{"id": "2bb34c51b6fe", "source": "https://www.xiaohongshu.com/explore/demo123", "source_kind": "url", "title": "外部工具下载的示例笔记", "duration_seconds": 1.865, "item_dir": "items/2bb34c51b6fe", "media_path": "items/2bb34c51b6fe/media.mp4", "subtitle_path": null, "subtitle_text_path": null, "subtitle_kind": null, "transcript_path": null, "transcript_text_path": null, "transcript_model": null, "transcript_language": null, "transcript_engine": null, "transcript_words_path": null, "transcript_diarized": null, "parent_source": null, "platform": "xiaohongshu", "uploader": "示例作者", "upload_date": "20261111", "thumbnail_path": null, "status": "succeeded", "error": null, "attempts": 0}
```

```markdown
# 素材索引

更新时间（UTC）：2026-11-11T06:10:21Z

共 1 条。

| ID | 标题 | 来源 | 时长 | 媒体 | 字幕 | 转写 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2bb34c51b6fe | 外部工具下载的示例笔记 | https://www.xiaohongshu.com/explore/demo123 | 0:02 | items/2bb34c51b6fe/media.mp4 |  |  | 成功 |
```

单条 B 站链接入库后的索引（时长为完整片源时长）：

```markdown
# 素材索引

更新时间（UTC）：2026-11-11T06:09:32Z

共 1 条。

| ID | 标题 | 来源 | 时长 | 媒体 | 字幕 | 转写 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 82685d1b16c2 | 【4K】《大雄兔(Big Buck Bunny)》（2008）动画短片 p02 1080P-30fps | https://www.bilibili.com/video/BV1DT4y1z7a1?p=2 | 10:35 | items/82685d1b16c2/media.mp4 |  |  | 成功 |
```

## 9. 从 Agent 使用 MCP 服务

默认的 `submit_batch` 只入队（`detach=true`），需要另开 worker 才会执行。进程内执行可传 `detach=false`。

Claude Desktop（`claude_desktop_config.json`）或 Cursor（`mcp.json`）可用类似片段：

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

先准备一座同时带字幕和转写的素材库：

```bash
media-intake run ./talk.mp4 ./speech.wav --library ./library-mcp \
  --transcribe missing --whisper-model tiny \
  --whisper-device cpu --whisper-language en --retries 0
```

```text
运行报告
  成功：2    跳过：0    失败：0    取消：0

成功：
  - [606d2ed035b7] talk
      来源：./talk.mp4
      媒体：items/606d2ed035b7/media.mp4
      字幕：items/606d2ed035b7/source.srt（source）
  - [c0bde6dfe568] speech
      来源：./speech.wav
      媒体：items/c0bde6dfe568/media.wav
      字幕：无来源字幕
      转写：items/c0bde6dfe568/transcript.srt（模型 tiny，语言 en）
```

下面的脚本通过官方 `mcp` SDK 连接同一套工具（与 stdio 服务暴露的工具一致）。实测执行：

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
        show("read_transcript (speech)", payload(await client.call_tool(
            "read_transcript", {"library": library, "id": speech_id, "kind": "auto", "limit": 200})))
        show("read_transcript (source subs)", payload(await client.call_tool(
            "read_transcript", {"library": library, "id": talk_id, "kind": "source", "limit": 200})))
        show("search_library", payload(await client.call_tool(
            "search_library", {"library": library, "query": "water is blue"})))
        submitted = payload(await client.call_tool(
            "submit_batch",
            {"items": [media], "library": library, "transcribe": "never", "detach": True},
        ))
        show("submit_batch", submitted)
        show("list_batches", payload(await client.call_tool("list_batches", {"library": library})))
        show("cancel_batch", payload(await client.call_tool(
            "cancel_batch", {"id": submitted["batch_id"], "library": library})))


if __name__ == "__main__":
    asyncio.run(main())
```

真实工具输出（已裁剪并改写路径）：

`list_tools`：`cancel_batch`、`get_metadata`、`get_status`、`list_batches`、`read_item`、`read_manifest`、`read_transcript`、`search_library`、`submit_batch`。

`read_transcript`（转写，全文短于 `limit`，故 `next_offset` 为 `null`）：

```json
{
  "kind": "transcript",
  "library": "./library-mcp",
  "id": "c0bde6dfe568",
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

同一座库里的来源字幕：

```json
{
  "kind": "transcript",
  "library": "./library-mcp",
  "id": "606d2ed035b7",
  "title": "talk",
  "text_kind": "source",
  "text": "这是一条来源字幕，用于对照离线转写。\n",
  "offset": 0,
  "limit": 200,
  "returned_length": 19,
  "total_length": 19,
  "next_offset": null
}
```

`search_library` 对 `water is blue` 命中 1 条，`matched_in` 为 `["transcript"]`，`snippet` 为 `The water is blue today.`。

默认入队并取消：

```json
{"batch_id": "batch-7ac00454644a4f6188481b455d0d2684", "status": "queued", "item_count": 1, "library": "./library-mcp", "detach": true}
```

```json
{
  "kind": "batch",
  "batch_id": "batch-7ac00454644a4f6188481b455d0d2684",
  "status": "cancelled",
  "error": "已取消",
  "exit_code": 1,
  "cancel_requested": true,
  "created_at": "2026-11-11T06:10:31Z",
  "updated_at": "2026-11-11T06:10:31Z"
}
```

时间戳为 UTC，对应 2026-11-11 14:10:31（亚洲/新加坡时间）。整段 MCP 调用约 0.9 秒。

## 10. 无 cookies 时访问抖音 / 小红书

未提供 cookies，仅记录本工具和 yt-dlp 回退的真实失败信息（不是可下载路径）：

```bash
media-intake run 'https://www.douyin.com/video/7153210116515532078' \
  --library ./library-cn-nocookie --retries 0
```

```text
失败：
  - [...] (无标题)
      来源：https://www.douyin.com/video/7153210116515532078
      原因：douyin 下载失败：未能从抖音公开页面解析出视频信息（桌面页常为脚本壳；未签名的 iteminfo 接口也可能返回 encrypt_data_miss。可提供 cookies 后重试；本工具不会生成签名）；回退到 yt-dlp 也失败：... ERROR: [Douyin] ... Fresh cookies (not necessarily logged in) are needed
```

```bash
media-intake run 'https://www.xiaohongshu.com/explore/64c7b0c00000000000000001' \
  --library ./library-cn-nocookie --retries 0
```

```text
失败：
  - [...] (无标题)
      来源：https://www.xiaohongshu.com/explore/64c7b0c00000000000000001
      原因：xiaohongshu 下载失败：小红书返回了安全校验页（反爬拦截）。可在本机浏览器登录后通过 --cookies 或 --cookies-from-browser 重试；本工具不会绕过签名或验证码；回退到 yt-dlp 也失败：... ERROR: [XiaoHongShu] ... No video formats found!
```

这与工具的既定边界一致：不做反爬绕过，也不轮换代理。需要这类内容时，用你信任的外部工具取到文件，再 `import`。

## 11. 与此前（可访问 YouTube 的环境）演示的差异

较早的 0.4.0 演示是在能够访问 YouTube 和 Hugging Face 官方地址的机器上完成的。本次中国大陆云主机上的主要差异是：

- YouTube 不可达，在线示例改用 B 站开放动画，并需要 `--user-agent yt-dlp` 才能避免 412。
- `github.com` 不能用于 `git clone` / `pip install git+https://...`，但 `codeload.github.com` 可以拉源码包；依赖走国内 PyPI 镜像。
- Hugging Face 官方不可达，必须设置 `HF_ENDPOINT=https://hf-mirror.com` 才能下载转写模型。
- 0.4.0 在 B 站默认下载流程上会被弹幕转换失败拖垮，0.4.1 已修复。
- 公开页往往没有可分离的对白字幕；「有字幕的链接」这一项在本环境只能改用本地侧车字幕验证。
- whisperX extra 过重，未在本机装完。

## 说明与限制

- 抖音 / 小红书等站点仍可能因登录墙或安全校验失败；本工具只诊断 cookie，不绕过反爬。
- MCP 默认只入队，需要同时运行 `media-intake worker --library <素材库>`。试用完毕请停下 worker。
- whisperX 是可选、较重的依赖；在 CPU 云主机上按默认方式安装很容易拉入 CUDA 轮子。
- 使用 `--download-sections` 时，清单时长可能仍是完整片源时长。
- `--ytdlp-option` 后接以 `-` 开头的参数时必须使用等号写法。
- 仓库尚未发布到 PyPI。Windows 为尽力支持。
