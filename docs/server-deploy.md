# 在 Linux 云服务器上部署 media-intake

本稿面向在一台普通 Linux 云主机上安装和使用本工具的情况，并记录在中国大陆网络环境下实测到的差异。不涉及打开额外端口、安装系统服务，或把 cookies、令牌写入仓库。

建议把虚拟环境、ffmpeg、素材库和模型缓存全部放在一个专用目录里，例如 `~/media-intake-trial`，不要改系统包或其它项目的文件。用完若只是临时试用，记得停掉 `media-intake worker`（若用 `--once` 则会自行退出）。

## 1. 准备目录与 Python

需要 Python 3.10+。下面假定使用 3.12，并且可以把文件写进自己的家目录。

```bash
mkdir -p ~/media-intake-trial
cd ~/media-intake-trial
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
```

若机器在中国大陆，把 PyPI 换成云厂商或高校镜像会快很多。实测腾讯云镜像和清华镜像都可用，前者在腾讯云主机上往往更快：

```bash
export PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
export PIP_TRUSTED_HOST=mirrors.cloud.tencent.com
# 备选：https://pypi.tuna.tsinghua.edu.cn/simple
```

也可把这两项写进 `pip.conf`，避免每次手动导出。

## 2. 没有 root 时安装 ffmpeg

本工具需要 `ffmpeg` 和 `ffprobe`。若有管理员权限，直接用发行版包装上即可，例如 Debian / Ubuntu：

```bash
sudo apt-get update && sudo apt-get install -y ffmpeg
```

若没有 sudo，不要改系统包。可选办法：

1. **从发行版仓库下载 deb / rpm 再解压到专用前缀**（在云厂商的系统镜像上通常很快）。把 `ffmpeg`、`ffprobe` 以及所需共享库解压到例如 `~/media-intake-trial/opt`，再用包装脚本设置 `LD_LIBRARY_PATH` 后调用。只要不执行 `dpkg -i` / `rpm -i`，就不需要 root。
2. **使用静态编译的 ffmpeg**。例如从常见的 Linux amd64 静态发布包解出两个可执行文件，放到 `~/media-intake-trial/bin`。在中国大陆，国外静态包站点有时很慢（实测约 20 KB/s），而云厂商的系统镜像仓库通常快几个数量级。也可以在一台访问更快的机器上下载后再拷过来。

然后把专用 `bin` 放到 `PATH` 前面：

```bash
export PATH="$HOME/media-intake-trial/bin:$HOME/media-intake-trial/.venv/bin:$PATH"
ffmpeg -version
ffprobe -version
```

## 3. 从 GitHub 安装（尚未发布到 PyPI）

仓库不在 PyPI 上。能直接访问 `github.com` 时，可以用：

```bash
python -m pip install "media-intake[whisper,mcp] @ git+https://github.com/hzopc1031/media-intake.git@main"
media-intake --version
```

在本次中国大陆云主机实测中，`https://github.com` 连接超时，`pip install git+https://github.com/...` 卡在 `git clone`，45 秒后被中止。同一时刻：

- `https://codeload.github.com/hzopc1031/media-intake/tar.gz/refs/heads/main` 可以下载源码包（约 100 KB 级，数秒内完成）；
- 依赖则从 PyPI 镜像安装，`[whisper,mcp]` 大约 20 秒完成。

因此可以采用：

```bash
mkdir -p ~/media-intake-trial/src
curl -L -o ~/media-intake-trial/src/media-intake-main.tar.gz \
  https://codeload.github.com/hzopc1031/media-intake/tar.gz/refs/heads/main
python -m pip install "~/media-intake-trial/src/media-intake-main.tar.gz[whisper,mcp]"
```

或先解压再 `pip install "./src/media-intake-main[whisper,mcp]"`。注意 `pip` 里带 `[]` 的路径需要加引号。

安装完成后运行：

```bash
media-intake doctor
```

`doctor` 会检查 ffmpeg、ffprobe、yt-dlp，以及可选的 faster-whisper、whisperx、mcp。缺少 whisperX 不影响下载、入库和默认转写。

## 4. 中国大陆网络环境

下列现象来自 2026-11-11 的一次实测，其它机房或线路可能不同，但值得先探测再开工。

| 目标 | 实测结果 | 建议 |
| --- | --- | --- |
| PyPI 官方 | 可访问，但偏慢 | 改用云厂商或清华等镜像 |
| `github.com`（网页与 `git clone`） | 连接超时 | 用 `codeload.github.com` 拉源码包，或在可访问 GitHub 的机器上取包再拷贝 |
| `codeload.github.com` | 可用 | 作为当前可用的 GitHub 源码通道 |
| YouTube | 连接失败（`Network is unreachable`） | 不要把演示或默认流程建立在 YouTube 上；改用你能访问且有权处理的来源 |
| Hugging Face 官方 | 连接超时 | `export HF_ENDPOINT=https://hf-mirror.com` |
| `hf-mirror.com` | 可用 | 下载 faster-whisper 模型前先设置；并建议 `export HF_HOME=$HOME/media-intake-trial/.hf`，避免模型写到家目录其它缓存里 |
| B 站 | 站点可访问，但见下一节 | 用 B 站或其它可访问平台做在线试验 |
| 抖音 / 小红书网站 | 站点可访问 | 无 cookies 时下载仍会失败，见 [docs/demo.md](demo.md) |

转写前最少导出：

```bash
export HF_ENDPOINT=https://hf-mirror.com
export HF_HOME="$HOME/media-intake-trial/.hf"
```

否则 faster-whisper 第一次拉 `tiny` 等模型会卡在官方地址。本次实测在设置镜像后，首次转写（含下载 tiny 模型）约 45 秒，同一模型再次使用约 3 秒。

## 5. B 站：User-Agent 与弹幕

在该云主机上，yt-dlp 默认的浏览器式 User-Agent 访问 B 站视频页会得到 **HTTP 412**。把 UA 设成工具自己的名字后可以继续解析和下载：

```bash
media-intake run 'https://www.bilibili.com/video/BVxxxxxxxxxxxx' \
  --ytdlp-option=--user-agent --ytdlp-option=yt-dlp \
  --format 'bv*[height<=360]+ba/b[height<=360]/worst' \
  --ytdlp-option=--download-sections --ytdlp-option='*0:00-0:08'
```

这只是如实声明客户端，不是伪装成浏览器，也不会绕过登录或签名。若 412 仍然出现，请改用你已有权获取的本地文件，再执行 `media-intake import`。

另外，yt-dlp 会把 B 站**弹幕**当成一种字幕语言 `danmaku`。本工具默认会把字幕转成 SRT；对 `danmaku.xml` 做这一转换会报 `Invalid data found when processing input`，并且 **yt-dlp 以非 0 退出，整次任务被判失败**（即使视频文件已经写下）。0.4.1 起默认使用 `--sub-langs all,-danmaku`。若你仍在使用 0.4.0，请显式加上：

```bash
media-intake run 'https://www.bilibili.com/video/BVxxxxxxxxxxxx' \
  --sub-langs 'all,-danmaku' \
  --ytdlp-option=--user-agent --ytdlp-option=yt-dlp
```

只有在确实想保存弹幕 XML 时，才使用 `--sub-langs danmaku`（此时不会再做 SRT 转换）。弹幕不是对白字幕，不能当成 `source.srt`。不少「官方双语」视频的字幕是烧进画面的，页面上也没有可下载的 CC 轨道。

`--ytdlp-option` 若后接以 `-` 开头的参数，必须写成 `--ytdlp-option=--foo`。`--download-sections` 里的 `*` 请加引号。切片后，清单里的时长仍可能是完整片源时长。

## 6. whisperX（可选，较重）

默认转写后端是 faster-whisper，对试用通常够用。`pip install 'media-intake[whisperx]'` 会再拉入 PyTorch、pyannote 等大型依赖，并且常见的是带 CUDA 的 GPU 轮子，体积以 GB 计。

本次在 4 核、约 8 GB 内存的 CPU 云主机上尝试安装时，pip 在解析与下载 CUDA / torch / transformers 依赖上花费超过 3 分钟仍未结束，被中止。中途还可能留下不完整的 nvidia 相关包，需要再卸载以免虚占数 GB 磁盘。若确实需要 whisperX，请：

- 使用单独的虚拟环境，以免冲掉已经可用的 faster-whisper；
- 预留足够磁盘和时间；
- 按你的机器选择 CPU 或 CUDA 版 PyTorch，而不是放任解析器拉来完整 GPU 栈；
- 说话人分离所需的 Hugging Face 令牌只放在环境变量里，不要写进命令行或仓库。

## 7. 运行与收尾

- 在线试验请只处理你有权获取的内容，并用 `--download-sections` 限制时长、用较低清晰度减小流量。
- `media-intake submit` 默认只入队，需要另开 `media-intake worker --library <素材库>`。临时试用加 `--once`，跑完即退。
- 不要把 cookies 文件、令牌或个人路径提交到 git。`cookies check` 只报告名称、域名和过期时间，不会打印值。
- 本工具不绕过登录墙、安全校验或签名参数，也不提供代理轮换。抖音 / 小红书在无 cookies 时失败是预期行为；可用外部工具取到文件后再 `import`。

更完整的命令、真实输出和 MCP 调用顺序见 [docs/demo.md](demo.md)。
