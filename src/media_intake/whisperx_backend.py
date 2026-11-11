"""可选的 whisperX 转写后端。本模块在未安装 whisperx 时也可安全导入。"""

from __future__ import annotations

import os
import sys

WHISPERX_INSTALL_HINT = (
    "未安装 whisperx，无法使用 whisperX 转写后端。这是可选功能，请先安装：\n"
    f"  {sys.executable} -m pip install 'media-intake[whisperx]'\n"
    "或：\n"
    f"  {sys.executable} -m pip install whisperx"
)

DIARIZE_TOKEN_HINT = (
    "说话人分离需要 Hugging Face 访问令牌（pyannote 模型）。"
    "请设置环境变量 MEDIA_INTAKE_HF_TOKEN、HF_TOKEN 或 HUGGING_FACE_HUB_TOKEN，"
    "不要把令牌写进命令行、配置文件或仓库。"
)


def whisperx_is_installed() -> bool:
    try:
        import whisperx  # noqa: F401
    except ImportError:
        return False
    return True


def hf_token() -> str | None:
    for key in ("MEDIA_INTAKE_HF_TOKEN", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    return None
