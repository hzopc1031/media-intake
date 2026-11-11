# media-intake

把**你自己的作品**，以及**平台条款或许可证允许下载的内容**，从公开链接或本地文件整理进本地素材库：排队处理、写入来源字幕，并生成机器可读清单和人类可读索引，方便交给 AI agent 使用。

本工具用于下载你自己的作品，以及平台条款或许可证允许下载的内容。

当前版本是 **0.4.1**：MCP 可以直接把字幕 / 转写正文和条目元数据交给模型（支持分页）；批次可交给独立 `worker` 运行，客户端退出后也能继续，并可查询与取消；转写可选用 whisperX（词级时间戳和可选说话人分离），默认仍是 faster-whisper；另外提供抖音 / 小红书 cookie 诊断，以及把外部工具已下载的文件导入素材库。逐步说明见 [docs/demo.md](docs/demo.md)。在 Linux 云服务器（含中国大陆网络环境）上部署见 [docs/server-deploy.md](docs/server-deploy.md)。

---

## 它做什么

1. 读取命令行、文本文件或标准输入中的条目，每行一个 `http(s)` 链接或本地路径。
2. 按可配置的并发数排队处理；失败按指数退避重试；状态存在素材库内的 SQLite 中，再次运行会跳过已经成功的条目。也可以只把任务写入队列（`media-intake submit` 或 MCP `submit_batch`），再由独立的 `media-intake worker` 领取执行；客户端退出后未完成批次仍可继续。队列支持查询进度、取消，以及回收心跳过期的运行中批次。
3. 批量选项在整个队列上生效：格式/清晰度、只提取音频、是否下载字幕、字幕语言。可以传入 Netscape 格式的 cookies 文件（`yt-dlp --cookies`），也可以用 `--cookies-from-browser BROWSER[:PROFILE]` 从本机浏览器读取 cookies。常用默认值可写在 `media-intake.toml` 或用户级配置里，命令行仍然覆盖配置。
4. 链接按站点选择后端。抖音（含 `v.douyin.com`、`iesdouyin.com` 分享页）和小红书（含 `xhslink.com` / `xhslink.cn`、国际站 `rednote.com`，以及图文笔记）会先走专用解析器：优先用移动端 UA 读取公开分享页里已有的嵌入数据，下载页面或公开接口已经提供的视频或图片地址；若页面单独给了 `download_addr`，会优先用它，**不会**改写播放地址去绕水印，也**不会**生成 X-Bogus、a_bogus、x-s 等签名参数。直播间会明确提示暂不支持。抖音桌面网页常返回脚本壳，公开的 `iteminfo` 接口也常因缺少签名（例如 `encrypt_data_miss`）而不可用；小红书短链则可能落到安全校验页或登录页。这些情况下专用后端会失败并回退到 yt-dlp。提供 `--cookies` 或 `--cookies-from-browser` 时，专用后端和 yt-dlp 都会带上。解析失败时回退到 [yt-dlp](https://github.com/yt-dlp/yt-dlp)（Unlicense），并记录两侧错误。其他站点直接走 yt-dlp。yt-dlp 调用默认带 `--no-playlist` 和 `--ignore-config`。加上 `--expand`（别名 `--playlist`）时，若专用后端能从公开主页里读到作品列表，会拆成独立队列条目；否则会明确提示「暂不支持展开」，并再尝试 yt-dlp。可选 `--max-items`。再次运行时按单条跳过已完成项。
5. 优先保存平台或文件自带的来源字幕，写成 `source.srt` 和对应纯文本 `source.txt`。没有字幕时如实记录「无来源字幕」。若启用了 `--transcribe`，会在本机写成 `transcript.srt` 和 `transcript.txt`（不覆盖来源字幕）。默认引擎是 faster-whisper；`--asr-backend whisperx` 时额外写出 `transcript.words.json`，并可按选项加入说话人标签。
6. 本地文件模式与下载模式一样完整：用 `ffprobe` 读取时长和标题，把文件复制（或按选项链接）进素材库，并收录同目录下的侧车字幕；本地音视频（包括仅音频文件）也可以按上面的规则转写。图文笔记里的图片不会拿去转写。也可用 `media-intake import` 把外部专用工具已经下载好的文件连同来源链接、标题等元数据导入素材库。
7. 运行结束打印报告（成功 / 跳过 / 失败 / 取消及原因）。只要有失败或取消，进程退出码不为 0。

下载能否成功，取决于目标页面是否仍嵌入可解析数据、本机 yt-dlp 对那个站点的支持，以及你是否有权下载该内容。平台反爬、签名校验或登录墙都可能导致专用后端和 yt-dlp 同时失败，这不是保证可下载的工具。本工具不承诺自研绕过反爬，也不提供代理轮换。

## 安装

需要 Python 3.10+，以及系统里的 **ffmpeg**（含 ffprobe）。yt-dlp 会作为 Python 依赖一并安装。离线转写、whisperX 和 stdio MCP 服务是可选功能。

```bash
# 安装 ffmpeg（任选一种）
# macOS:
brew install ffmpeg
# Debian / Ubuntu（需要管理员权限）：
sudo apt-get update && sudo apt-get install -y ffmpeg

# 仓库尚未发布到 PyPI。能访问 GitHub 时：
python3 -m pip install "media-intake[whisper,mcp] @ git+https://github.com/hzopc1031/media-intake.git@main"
# 若 github.com 被拦截，可改用 GitHub 的 codeload 源，详见 docs/server-deploy.md。

# 从本地源码安装时：
python3 -m pip install -e .
# 若要使用默认的 faster-whisper 离线转写：
python3 -m pip install -e ".[whisper]"
# 若要使用 whisperX（词级时间戳，可选说话人分离；依赖较重）：
python3 -m pip install -e ".[whisperx]"
# 若要作为 MCP 服务使用：
python3 -m pip install -e ".[mcp]"
```

尚未发布到 PyPI。请从本仓库安装。第一次转写时，所选引擎可能会从 Hugging Face 下载模型，耗时和体积取决于模型大小（`tiny` 较小，`large-v3` 很大），需要本机有足够磁盘和内存。本工具不会替你预装模型。

检查依赖，以及可选的 cookies：

```bash
media-intake doctor
media-intake doctor --cookies ./cookies.txt
media-intake cookies check --cookies ./cookies.txt
# 或：
media-intake cookies check --cookies-from-browser chrome
```

升级 yt-dlp（优先用当前 Python 的 pip；失败时再尝试独立可执行文件的 `yt-dlp -U`）。本工具不会自行下载未校验校验和的二进制文件：

```bash
media-intake update-yt-dlp
```

## 用法

```bash
# 单条本地文件（当前进程里立即处理）
media-intake run ./talk.mp4 --library ./media-library

# 单条链接
media-intake run 'https://example.com/watch/123' --library ./media-library

# 批量：文本文件，每行一个条目；# 开头为注释
media-intake run --input urls.txt --library ./media-library \
  --concurrency 2 --format 'best[height<=720]' --sub-langs zh,en

# 只提取音频、关闭字幕
media-intake run --input urls.txt --audio-only --no-subs

# 标准输入
media-intake run --input - --library ./media-library < urls.txt

# 使用 Netscape 格式 cookies 文件（不要把该文件提交到 git）
media-intake run --input urls.txt --cookies ./cookies.txt

# 或从本机浏览器读取 cookies（格式与 yt-dlp 相同：BROWSER[:PROFILE]）
media-intake run --input urls.txt --cookies-from-browser chrome:Default

# 展开播放列表或主页，每条视频单独入队；最多展开 20 条
media-intake run 'https://example.com/playlist/123' --expand --max-items 20

# 额外把单个参数传给 yt-dlp（可重复；若参数以 - 开头请用等号写法）
media-intake run 'https://example.com/watch/123' \
  --ytdlp-option=--username --ytdlp-option YOUR_NAME

# 没有来源字幕时，用 faster-whisper 转写（默认模型 base，语言自动检测）
media-intake run ./talk.mp4 --library ./media-library --transcribe

# 指定模型、设备和语言；即使已有来源字幕也转写
media-intake run ./talk.m4a --transcribe always \
  --whisper-model tiny --whisper-device cpu --whisper-language en

# 改用 whisperX，写出词级时间戳
media-intake run ./talk.mp4 --transcribe always \
  --asr-backend whisperx --whisper-model tiny

# 说话人分离：先在当前 shell 设置 MEDIA_INTAKE_HF_TOKEN 或 HF_TOKEN，不要写进仓库或命令行历史
media-intake run ./interview.mp4 --transcribe always \
  --asr-backend whisperx --diarize --whisper-model tiny

# 只入队，交由独立 worker 处理（客户端退出后仍会继续）
media-intake submit --input urls.txt --library ./media-library --transcribe
media-intake worker --library ./media-library
# 查询、列出、取消
media-intake status batch-... --library ./media-library
media-intake batches --library ./media-library
media-intake cancel batch-... --library ./media-library

# 把外部工具已下载的文件导入素材库
media-intake import ./downloaded.mp4 --library ./media-library \
  --source-url 'https://www.xiaohongshu.com/explore/...' \
  --title '笔记标题' --uploader '作者' --platform xiaohongshu

# 重新处理已成功的条目
media-intake run --input urls.txt --force
```

`media-intake run --help` 查看全部选项。命令行帮助是中文的，并同样包含上面的用途说明。

可选配置文件（后出现的覆盖先出现的，命令行优先）：`~/.config/media-intake/config.toml`、当前目录的 `media-intake.toml`，或 `--config FILE`。不要把令牌、cookie 内容或本机个人路径提交到 git。示例：

```toml
library = "media-library"
concurrency = 2
asr_backend = "faster-whisper"
whisper_model = "base"
# cookies_from_browser = "chrome"
```

## 素材库目录结构

默认库目录是当前工作目录下的 `media-library/`。路径在清单里都写成相对库根目录的 POSIX 路径。

```
media-library/
  manifest.jsonl                 # JSON Lines，每行一条记录
  index.md                       # Markdown 索引
  .media-intake/state.sqlite     # 队列、批次与断点续跑状态
  .media-intake/batches/         # 批次状态的 JSON 副本，便于查看
  items/<id>/
    media.<ext>                  # 视频或音频
    source.srt                   # 来源字幕（如有）
    source.txt                   # 来源字幕纯文本（如有）
    transcript.srt               # 离线转写（启用 --transcribe 且成功时）
    transcript.txt
    transcript.words.json        # whisperX 词级时间戳（若启用）
    *.info.json                  # yt-dlp 写入的元数据（下载模式）
```

`<id>` 是来源身份的 SHA-256 十六进制前 12 位：链接会先做轻度规范化（小写主机名、去掉常见追踪参数），本地文件使用解析后的绝对路径。这样再次运行可以稳定跳过已完成项。请不要把整个素材库提交到版本库。

`manifest.jsonl` 字段：`id`、`source`、`source_kind`（`url` 或 `local`）、`title`、`duration_seconds`、`item_dir`、`media_path`、`subtitle_path`、`subtitle_text_path`、`subtitle_kind`（`source`、`auto` 或空）、`transcript_path`、`transcript_text_path`、`transcript_model`、`transcript_language`、`transcript_engine`、`transcript_words_path`、`transcript_diarized`、`parent_source`（由播放列表或主页展开而来时记录父链接，否则为空）、`platform`、`uploader`、`upload_date`、`thumbnail_path`、`status`、`error`、`attempts`。每次运行后按库内全部记录重写清单和索引，不会因重复运行而追加重复行。索引的「转写」列在有结果时会附上引擎、模型和语言，若已做说话人分离会标明。

## Cookies、展开与站点后端

- **Cookies 文件：** `--cookies FILE`，把 Netscape 格式 cookies 文件交给 yt-dlp。工具不会读取或打印文件内容用于日志；`cookies check` 只会报告名称、域名和过期时间。仓库的 `.gitignore` 已忽略 `*.cookies`、`cookies*.txt` 等路径，请不要提交 cookie 文件或密钥。
- **浏览器 cookies：** `--cookies-from-browser BROWSER[:PROFILE]`，直接传给 yt-dlp，例如 `chrome`、`firefox`、`chrome:Default`。本工具不保存、不打印浏览器里的 cookie 内容。
- **Cookie 诊断：** `media-intake cookies check`（或 `doctor --cookies`）会检查抖音、小红书的登录相关字段是否存在或过期，并给出下一步建议。诊断通过不表示站点一定放行。
- **外部导入：** 若这两个站点仍然被反爬或安全校验拦住，可用你信任的专用工具先取到文件，再 `media-intake import` 入库。本工具不会绕过登录墙，也不生成签名参数。
- **额外参数：** `--ytdlp-option` 可把单个参数原样传给 yt-dlp（可重复）。若参数本身以 `-` 开头，写成 `--ytdlp-option=--foo`（argparse 否则会把 `--foo` 当成另一个选项）。排在本工具默认参数之后，因此可以覆盖它们。
- **展开：** 默认不展开播放列表或主页。`--expand`（别名 `--playlist`）把其中每个视频变成独立队列条目，可选 `--max-items N` 限制每个输入链接展开的数量。断点续跑和跳过按单条视频计算。
- **站点后端：** 下载与展开通过 `media_intake.extractors` 按 URL 选择后端。抖音、小红书专用解析器排在 yt-dlp 之前；失败时回退到 yt-dlp。专用解析器只读取公开页或公开接口已经给出的地址，不绕签名、不轮换代理。创作者主页的 `--expand` 仅在公开页已嵌入作品列表时可用。
- 本工具不提供代理轮换。

## 离线转写

转写是可选功能，默认关闭。启用后在本机推理，不把音视频送到第三方转写接口。

- 安装：默认后端需要 `python3 -m pip install 'media-intake[whisper]'`；whisperX 后端需要 `python3 -m pip install 'media-intake[whisperx]'`。未安装就使用对应 `--transcribe` / `--asr-backend` 时，命令会立即失败并打印安装提示。
- 模式：`--transcribe never`（默认，不转写）；`--transcribe` 或 `--transcribe missing`（没有来源字幕时转写）；`--transcribe always`（即使已有来源字幕也转写，不会覆盖 `source.srt`）。
- 后端：`--asr-backend faster-whisper`（默认，较轻）或 `--asr-backend whisperx`。whisperX 会写出 `transcript.words.json`。`--diarize` 仅适用于 whisperX，会在字幕和纯文本中加入说话人标签。说话人分离需要环境变量 `MEDIA_INTAKE_HF_TOKEN`、`HF_TOKEN` 或 `HUGGING_FACE_HUB_TOKEN`，不要把令牌写进命令行历史或仓库。
- 模型：`--whisper-model`，可以是 `tiny`、`base`（默认）、`small`、`medium`、`large-v3` 等公开模型名，或本机已有的模型目录。`--whisper-device` 默认 `auto`（有 CUDA 则用 GPU，否则 CPU）。`--whisper-compute-type` 默认 `auto`（CPU 用 `int8`，CUDA 用 `float16`）。`--whisper-language` 默认 `auto`，由模型检测。
- 适用范围：下载后的视频/音频，以及本地文件（包括只提取了音频的文件）。图文笔记中的图片会跳过转写，不视为失败。faster-whisper 转写前会用本机 ffmpeg 把媒体解成 16 kHz 单声道 PCM。
- 识别效果取决于模型和音频质量，不能保证字字正确。第一次使用某个模型时可能会下载权重，失败时条目会记为失败并留下错误原因。
- `media-intake doctor` 会检查 faster-whisper、whisperx 和 MCP SDK 是否已安装，但它们都是可选项：未安装时自检仍可通过，只提示如何安装。

## 后台批次与 worker

`media-intake run` 仍在当前进程里处理并等待结束。若希望提交后立刻返回、或让 MCP 客户端退出后任务继续跑：

1. 用 `media-intake submit`（或 MCP `submit_batch`，默认 `detach=true`）把批次写入素材库队列，得到 `batch_id`。
2. 在同一台机器上启动 `media-intake worker --library ./media-library`（别名 `serve`）。可用 `--once` 只领一条后退出，适合测试。
3. 用 `media-intake status` / `batches` 或 MCP `get_status` / `list_batches` 查看进度，用 `cancel` / `cancel_batch` 取消尚未结束的批次。排队中的会立即取消；运行中的会尽快停止尚未开始的条目。
4. worker 会定期写心跳。若进程崩溃、心跳超过 `--stale-after`（默认 120 秒），其他 worker 可以回收该批次并重新执行（已成功的条目默认仍会跳过）。

需要与旧行为一样在 MCP 当前进程里跑时，调用 `submit_batch` 时传入 `detach=false`。客户端退出后，这种方式下未完成的批次不会继续。

## MCP 服务

MCP 服务是可选功能。安装 `media-intake[mcp]` 后，可用 stdio 方式启动服务器，把同一套核心库暴露给 MCP 客户端。工具包括：

- `submit_batch`：提交一批链接或本地文件。默认只入队并立即返回 `batch_id`（`detach=true`），需另开 `media-intake worker` 处理。传入 `detach=false` 时在当前进程的后台线程运行。选项包括 `format`、`audio_only`、`subs`、`sub_langs`、`transcribe`、`asr_backend`、`diarize`、`cookies`（本机 cookies 文件路径）、`cookies_from_browser`、`expand`、`max_items`，以及与命令行对应的 whisper 参数。
- `get_status`：按 `batch_id` 或条目 ID 查询。返回计数，以及各条目的状态和错误。查询单条条目时需要同时提供 `library`。
- `list_batches`：列出素材库中的批次。
- `cancel_batch`：取消尚未结束的批次。
- `read_manifest`：读取素材库的 `manifest.jsonl`。可用 `status`、`source_kind` 过滤，并用 `limit` 限制条数。
- `read_item`：读取已入库条目的元数据，包括正文是否可读和正文长度，不把整篇正文塞进结果。
- `read_transcript`：把来源字幕或转写纯文本直接返回给模型。`kind` 为 `auto`（优先转写）、`transcript` 或 `source`。按字符分页，返回 `total_length` 与 `next_offset`。
- `search_library`：按标题、来源、ID 或正文片段检索。
- `get_metadata`：读取已入库条目，或在不下载的情况下探测链接 / 本地文件的标题、时长和是否有字幕。

启动方式任选其一：`media-intake mcp`、`media-intake-mcp`，或 `python3 -m media_intake.mcp_server`。服务走标准输入输出，请由 MCP 客户端拉起，不要在终端里交互使用。未安装 `mcp` 时，这些入口会提示如何安装并以退出码 2 结束。cookies 文件路径可以传给工具，但服务不会读取或打印 cookie 内容。批次状态写在素材库的 SQLite 和 `.media-intake/batches/` 下，便于同一素材库再次查询。

在 MCP 客户端里的通用配置示例（请把命令换成你环境里实际可用的解释器或安装入口，不要写入本机个人路径）：

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

也可以用模块入口：

```json
{
  "mcpServers": {
    "media-intake": {
      "command": "python3",
      "args": ["-m", "media_intake.mcp_server"]
    }
  }
}
```

更完整的调用顺序和真实输出示例见 [docs/demo.md](docs/demo.md)。请另外启动 `media-intake worker --library <素材库>`，否则默认入队的批次不会开始执行。

## 测试

默认测试全部离线、可重复、较快：使用 ffmpeg 在本地生成的短视频和侧车 `.srt`，用 mock 代替 yt-dlp、faster-whisper 和 whisperX。MCP 工具处理函数的单测不依赖真实媒体；进程内往返测试只用本地生成的短视频。`make test` 与 CI **不会**从真实平台下载，也**不会**下载 Whisper / whisperX 模型。开发依赖里包含官方 `mcp` SDK，以便默认测试覆盖 MCP 往返。

```bash
python3 -m pip install -e ".[dev]"
make test
# 本环境如果没有 make，也可以：
python3 -m pytest -m "not online and not slow and not whisperx"
```

可选的真实模型转写测试（会在未缓存时下载 `tiny` 模型）用 pytest 标记 `slow`：

```bash
python3 -m pip install -e ".[dev,whisper]"
MEDIA_INTAKE_WHISPER=1 make test-whisper
```

可选的真实 whisperX 测试：

```bash
python3 -m pip install -e ".[dev,whisperx]"
MEDIA_INTAKE_WHISPERX=1 make test-whisperx
```

可选的联网测试用 pytest 标记 `online`，并且需要显式设置环境变量后才会真正执行：

```bash
MEDIA_INTAKE_ONLINE=1 make test-online
# 或
MEDIA_INTAKE_ONLINE=1 python3 -m pytest -m online
```

可把要测的链接放在环境变量 `MEDIA_INTAKE_ONLINE_URL`、`MEDIA_INTAKE_ONLINE_DOUYIN_URL` 或 `MEDIA_INTAKE_ONLINE_XHS_URL` 中。只有你确认该链接允许下载时才运行。平台反爬或签名校验失败时，这些测试会失败并留下错误信息，这是预期现象，不要把它写成离线夹具。

CI 在 GitHub Actions 上对 Linux 和 macOS、Python 3.10 与 3.13 运行 `make test`。Windows 作为尽力支持，不提供安装包。

## 开发说明

核心逻辑在 `media_intake.pipeline.run_batch`，与命令行入口和 MCP 入口共享。MCP 工具处理函数在 `media_intake.batches.BatchManager`，不依赖 MCP SDK；`media_intake.mcp_server` 只做薄封装。队列与 worker 在 `media_intake.batch_store`、`media_intake.worker`。转写接口在 `media_intake.transcribe.Transcriber`，本版本提供 faster-whisper 与可选的 whisperX 实现。站点后端接口在 `media_intake.extractors`。

本仓库以 MIT 许可证发布。参考过开源下载器在批量参数、失败报告、素材清单和依赖自检方面的产品缺口，但实现与代码均为独立编写，没有复制或改写其它 GPL 项目的源码。底层下载仍调用 yt-dlp。

---

## English

`media-intake` is a small Python CLI that queues **your own work** and **content you are allowed to download under the platform terms or license**, from public links or local files, into a local material library. It writes source subtitles when they exist, plus a JSONL manifest and a Markdown index, so another tool or AI agent can pick the files up.

This tool is intended for downloading your own work and content that the platform terms or license allow you to download.

Version 0.4.1 fixes a Bilibili danmaku conversion failure that could abort an otherwise successful download. Version 0.4.0 added MCP tools that return transcript/subtitle text and item metadata (`read_item`, `read_transcript` with character pagination, `search_library`, `get_metadata`); a detachable `media-intake worker` / `serve` so batches keep running after the MCP client exits, plus `submit`, `status`, `cancel`, and `list_batches`; an optional whisperX backend (`pip install 'media-intake[whisperx]'`, `--asr-backend whisperx`) that writes `transcript.words.json` and can label speakers (`--diarize`, HF token from the environment only); cookie diagnostics for Douyin and Xiaohongshu (`cookies check`); and `media-intake import` for files already fetched by an external tool. An optional `media-intake.toml` supplies defaults. See [docs/demo.md](docs/demo.md) for a walkthrough and [docs/server-deploy.md](docs/server-deploy.md) for Linux cloud and China-region notes. This tool does not bypass anti-bot checks and does not rotate proxies.

Version 0.3.0 added a minimal stdio MCP server (`pip install 'media-intake[mcp]'`, then `media-intake mcp`). Version 0.2.0 added optional offline transcription with faster-whisper (`pip install 'media-intake[whisper]'`). Version 0.1.1 added dedicated Douyin and Xiaohongshu backends (registered before yt-dlp, with yt-dlp fallback). They prefer a mobile UA, parse public share pages, and only use media URLs already present on the page or a public API; they do not rewrite watermarked URLs or generate signing parameters. Livestreams are reported as unsupported.

Install ffmpeg yourself, then `python3 -m pip install -e .`. The project is not on PyPI yet. Run `media-intake doctor` to check ffmpeg, yt-dlp, and optional transcribers and the MCP SDK, and `media-intake run --help` for options (help text is in Chinese). Cookie files are gitignored and never logged. Default tests (`make test`) and CI stay offline and do not download Whisper models. Optional tiny-model tests are `MEDIA_INTAKE_WHISPER=1 make test-whisper`. Optional live-platform tests are `MEDIA_INTAKE_ONLINE=1 make test-online`.

