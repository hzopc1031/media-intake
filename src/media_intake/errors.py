class IntakeError(Exception):
    """工具可预期的错误。"""


class DependencyError(IntakeError):
    """缺少或无法运行 ffmpeg / yt-dlp 等依赖。"""


class InputError(IntakeError):
    """输入条目无效。"""


class JobError(IntakeError):
    """单条任务失败（下载、探测或入库）。"""
