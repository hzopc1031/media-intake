# 更新日志

本项目版本号遵循语义化版本。仓库尚未发布到 PyPI。

## 0.4.1 — 2026-11-11

- 默认排除 B 站弹幕 `danmaku`：yt-dlp 把它当作字幕语言，`--convert-subs srt` 会失败，并把整次下载判为失败（即使媒体文件已经写完）。若明确指定 `--sub-langs danmaku`，则不再做 SRT 转换。
- 在一台中国大陆 Linux 云服务器上按真实用户路径做了端到端实测，并更新 [docs/demo.md](docs/demo.md)。
- 新增 [docs/server-deploy.md](docs/server-deploy.md)：在 Linux 云主机上部署，以及中国大陆网络环境下的 GitHub / PyPI / Hugging Face / YouTube / B 站注意事项。

## 0.4.0 — 2026-11-11

### MCP 正文与元数据

- 新增 `read_item`、`read_transcript`、`search_library`、`get_metadata`。
- `read_transcript` 把来源字幕或转写纯文本直接返回给模型，按字符分页，并给出 `total_length` 与 `next_offset`。
- `get_metadata` 可读取已入库条目，或在不下载的情况下探测链接 / 本地文件。
- 保留 `submit_batch`、`get_status`、`read_manifest`。

### 可脱离客户端的批次

- `submit_batch` 与新的 `media-intake submit` 默认只把任务写入素材库的 SQLite 队列。
- 新增 `media-intake worker`（别名 `serve`）：领取排队中的批次，写入心跳，并回收心跳过期的运行中批次。
- 新增 `media-intake status`、`cancel`、`batches`，以及 MCP 工具 `cancel_batch`、`list_batches`。
- 进程内后台线程路径仍然可用（`detach=false`）。

### 可选 whisperX 转写后端

- 新增 extra `[whisperx]`，通过 `--asr-backend whisperx` 选用。
- 默认仍是更快、更轻的 `faster-whisper`。
- whisperX 会写出 `transcript.words.json` 词级时间戳；`--diarize` 时可在 SRT/TXT 中加入说话人标签。
- 说话人分离所需的 Hugging Face 令牌只从环境变量读取，不会写入仓库。
- 真实 whisperX 测试需设置 `MEDIA_INTAKE_WHISPERX=1`。

### 抖音 / 小红书 cookie 诊断与外部文件导入

- 新增 `media-intake cookies check`，`doctor` 也可传入 `--cookies` / `--cookies-from-browser`。
- 只报告名称、域名和过期时间，不打印 cookie 值。
- 新增 `media-intake import`，把外部专用工具已下载的文件连同来源链接和标题等元数据写入素材库。
- 不提供反爬绕过或代理轮换。

### 其它

- 支持可选的 `media-intake.toml` 与用户级配置；命令行选项仍然覆盖配置。
- 清单补充 `platform`、`uploader`、`upload_date`、`thumbnail_path` 以及转写引擎、词级文件和说话人标记等字段。
- 修复 yt-dlp 2026.10.26 下播放列表 / 频道展开所用的 print 模板。
- `--ytdlp-option` 若后接以 `-` 开头的参数，需写成 `--ytdlp-option=--foo`。
- 使用说明见 [docs/demo.md](docs/demo.md)。

## 0.3.0 — 2026-11-11

- 增加可选的 stdio MCP 服务（`submit_batch`、`get_status`、`read_manifest`）。

## 0.2.0 — 2026-11-11

- 增加可选的 faster-whisper 离线转写。

## 0.1.1 — 2026-11-11

- 增加抖音、小红书专用后端，失败时回退 yt-dlp。

## 0.1.0 — 2026-11-11

- 首个可用版本：队列、素材库、来源字幕与清单。
