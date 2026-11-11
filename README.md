# media-intake

把**你自己的作品**，以及**平台条款或许可证允许下载的内容**，从公开链接或本地文件整理进本地素材库：排队处理、写入来源字幕，并生成机器可读清单和人类可读索引，方便交给 AI agent 使用。

本工具用于下载你自己的作品，以及平台条款或许可证允许下载的内容。

当前版本是 **0.1.1**：在第一步基础上，已接入抖音和小红书的专用提取后端（按 URL 优先于 yt-dlp）。离线转写（faster-whisper）和 stdio MCP 服务计划在后续版本提供，本版本已留好调用接口，但尚未实现。

---

## 它做什么

1. 读取命令行、文本文件或标准输入中的条目，每行一个 `http(s)` 链接或本地路径。
2. 按可配置的并发数排队处理；失败按指数退避重试；状态存在素材库内的 SQLite 中，再次运行会跳过已经成功的条目。
3. 批量选项在整个队列上生效：格式/清晰度、只提取音频、是否下载字幕、字幕语言。可以传入 Netscape 格式的 cookies 文件（`yt-dlp --cookies`），也可以用 `--cookies-from-browser BROWSER[:PROFILE]` 从本机浏览器读取 cookies。
4. 链接按站点选择后端。抖音（含 `v.douyin.com`、`iesdouyin.com` 分享页）和小红书（含 `xhslink.com` / `xhslink.cn`、国际站 `rednote.com`，以及图文笔记）会先走专用解析器：优先用移动端 UA 读取公开分享页里已有的嵌入数据，下载页面或公开接口已经提供的视频或图片地址；若页面单独给了 `download_addr`，会优先用它，**不会**改写播放地址去绕水印，也**不会**生成 X-Bogus、a_bogus、x-s 等签名参数。直播间会明确提示暂不支持。抖音桌面网页常返回脚本壳，公开的 `iteminfo` 接口也常因缺少签名（例如 `encrypt_data_miss`）而不可用；小红书短链则可能落到安全校验页或登录页。这些情况下专用后端会失败并回退到 yt-dlp。提供 `--cookies` 或 `--cookies-from-browser` 时，专用后端和 yt-dlp 都会带上。解析失败时回退到 [yt-dlp](https://github.com/yt-dlp/yt-dlp)（Unlicense），并记录两侧错误。其他站点直接走 yt-dlp。yt-dlp 调用默认带 `--no-playlist` 和 `--ignore-config`。加上 `--expand`（别名 `--playlist`）时，若专用后端能从公开主页里读到作品列表，会拆成独立队列条目；否则会明确提示「暂不支持展开」，并再尝试 yt-dlp。可选 `--max-items`。再次运行时按单条跳过已完成项。
5. 优先保存平台或文件自带的来源字幕，写成 `source.srt` 和对应纯文本 `source.txt`。没有字幕时，本版本如实记录「无来源字幕」，不会假装成功生成了字幕。
6. 本地文件模式与下载模式一样完整：用 `ffprobe` 读取时长和标题，把文件复制（或按选项链接）进素材库，并收录同目录下的侧车字幕。
7. 运行结束打印报告（成功 / 跳过 / 失败及原因）。只要有失败，进程退出码不为 0。

下载能否成功，取决于目标页面是否仍嵌入可解析数据、本机 yt-dlp 对那个站点的支持，以及你是否有权下载该内容。平台反爬、签名校验或登录墙都可能导致专用后端和 yt-dlp 同时失败，这不是保证可下载的工具。

## 安装

需要 Python 3.10+，以及系统里的 **ffmpeg**（含 ffprobe）。yt-dlp 会作为 Python 依赖一并安装。

```bash
# 安装 ffmpeg（任选一种）
# macOS:
brew install ffmpeg
# Debian / Ubuntu:
sudo apt-get update && sudo apt-get install -y ffmpeg

python3 -m pip install -e .
```

尚未发布到 PyPI。请从本仓库安装。

检查依赖：

```bash
media-intake doctor
```

升级 yt-dlp（优先用当前 Python 的 pip；失败时再尝试独立可执行文件的 `yt-dlp -U`）。本工具不会自行下载未校验校验和的二进制文件：

```bash
media-intake update-yt-dlp
```

## 用法

```bash
# 单条本地文件
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

# 额外把单个参数传给 yt-dlp（可重复；适合登录相关选项）
media-intake run 'https://example.com/watch/123' \
  --ytdlp-option --username --ytdlp-option YOUR_NAME

# 重新处理已成功的条目
media-intake run --input urls.txt --force
```

`media-intake run --help` 查看全部选项。命令行帮助是中文的，并同样包含上面的用途说明。

## 素材库目录结构

默认库目录是当前工作目录下的 `media-library/`。路径在清单里都写成相对库根目录的 POSIX 路径。

```
media-library/
  manifest.jsonl                 # JSON Lines，每行一条记录
  index.md                       # Markdown 索引
  .media-intake/state.sqlite     # 队列与断点续跑状态
  items/<id>/
    media.<ext>                  # 视频或音频
    source.srt                   # 来源字幕（如有）
    source.txt                   # 来源字幕纯文本（如有）
    transcript.srt               # 预留给后续离线转写
    transcript.txt
    *.info.json                  # yt-dlp 写入的元数据（下载模式）
```

`<id>` 是来源身份的 SHA-256 十六进制前 12 位：链接会先做轻度规范化（小写主机名、去掉常见追踪参数），本地文件使用解析后的绝对路径。这样再次运行可以稳定跳过已完成项。请不要把整个素材库提交到版本库。

`manifest.jsonl` 字段：`id`、`source`、`source_kind`（`url` 或 `local`）、`title`、`duration_seconds`、`item_dir`、`media_path`、`subtitle_path`、`subtitle_text_path`、`subtitle_kind`（`source`、`auto` 或空）、`transcript_path`、`transcript_text_path`、`parent_source`（由播放列表或主页展开而来时记录父链接，否则为空）、`status`、`error`、`attempts`。每次运行后按库内全部记录重写清单和索引，不会因重复运行而追加重复行。

## Cookies、展开与站点后端

- **Cookies 文件：** `--cookies FILE`，把 Netscape 格式 cookies 文件交给 yt-dlp。工具不会读取或打印文件内容；日志里该参数的值显示为 `<redacted>`。仓库的 `.gitignore` 已忽略 `*.cookies`、`cookies*.txt` 等路径，请不要提交 cookie 文件或密钥。
- **浏览器 cookies：** `--cookies-from-browser BROWSER[:PROFILE]`，直接传给 yt-dlp，例如 `chrome`、`firefox`、`chrome:Default`。本工具不保存、不打印浏览器里的 cookie 内容。
- **额外参数：** `--ytdlp-option` 可把单个参数原样传给 yt-dlp（可重复）。排在本工具默认参数之后，因此可以覆盖它们。
- **展开：** 默认不展开播放列表或主页。`--expand`（别名 `--playlist`）把其中每个视频变成独立队列条目，可选 `--max-items N` 限制每个输入链接展开的数量。断点续跑和跳过按单条视频计算。
- **站点后端：** 下载与展开通过 `media_intake.extractors` 按 URL 选择后端。抖音、小红书专用解析器排在 yt-dlp 之前；失败时回退到 yt-dlp。专用解析器只读取公开页或公开接口已经给出的地址，不绕签名、不轮换代理。创作者主页的 `--expand` 仅在公开页已嵌入作品列表时可用。
- 本工具不提供代理轮换。

## 测试

默认测试全部离线、可重复，使用 ffmpeg 在本地生成的短视频和侧车 `.srt`，并用 mock 代替 yt-dlp。`make test` 与 CI **不会**从真实平台下载。

```bash
python3 -m pip install -e ".[dev]"
make test
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

核心逻辑在 `media_intake.pipeline.run_batch`，与命令行入口分离，便于后续做 stdio MCP（提交批次、查询状态、读取清单）。转写钩子在 `media_intake.transcribe.Transcriber`；本版本不会调用具体引擎。站点后端接口在 `media_intake.extractors`。

本仓库以 MIT 许可证发布。参考过开源下载器在批量参数、失败报告、素材清单和依赖自检方面的产品缺口，但实现与代码均为独立编写，没有复制或改写其它 GPL 项目的源码。底层下载仍调用 yt-dlp。

---

## English

`media-intake` is a small Python CLI that queues **your own work** and **content you are allowed to download under the platform terms or license**, from public links or local files, into a local material library. It writes source subtitles when they exist, plus a JSONL manifest and a Markdown index, so another tool or AI agent can pick the files up.

This tool is intended for downloading your own work and content that the platform terms or license allow you to download.

Version 0.1.1 adds dedicated Douyin and Xiaohongshu backends (registered before yt-dlp, with yt-dlp fallback). They prefer a mobile UA, parse public share pages, and only use media URLs already present on the page or a public API; they do not rewrite watermarked URLs or generate signing parameters. Livestreams are reported as unsupported. Douyin's desktop shell and unsigned iteminfo API, and Xiaohongshu's security-check or login pages, are treated as failures and fall back to yt-dlp. Profile expansion works only if the public page already embeds a work list; otherwise it is reported as unsupported and yt-dlp may be tried. Step 1 also includes queueing, per-batch options, source subtitles, local-file archiving, `doctor` / `update-yt-dlp`, `--cookies` and `--cookies-from-browser`. Offline transcription and a stdio MCP server are still planned. Proxy rotation is not provided.

Install ffmpeg yourself, then `python3 -m pip install -e .`. The project is not on PyPI yet. Run `media-intake doctor` to check ffmpeg and yt-dlp, and `media-intake run --help` for options (help text is in Chinese). Cookie files are gitignored and never logged. Default tests (`make test`) and CI stay offline. Optional live-platform tests are `MEDIA_INTAKE_ONLINE=1 make test-online`.
