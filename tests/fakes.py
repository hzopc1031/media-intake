from __future__ import annotations

import json
from pathlib import Path

from media_intake.process import CommandResult


class FakeRunner:
    """拦截 ffmpeg / ffprobe / yt-dlp，不访问网络。"""

    def __init__(
        self,
        *,
        playlist: dict[str, list[dict]] | None = None,
        fail_urls: set[str] | None = None,
        fail_expand: set[str] | None = None,
    ) -> None:
        self.playlist = playlist or {}
        self.fail_urls = fail_urls or set()
        self.fail_expand = fail_expand or set()
        self.calls: list[list[str]] = []
        self.downloads: list[str] = []

    def run(self, argv, *, cwd=None, timeout=None) -> CommandResult:
        argv = list(argv)
        self.calls.append(argv)
        name = Path(argv[0]).name
        if len(argv) > 1 and argv[1] in {"-version", "--version"}:
            return CommandResult(argv, 0, f"{name} 1.0\n", "")
        if name == "ffprobe":
            payload = {"format": {"duration": "2.0", "tags": {"title": "Fake Title"}}}
            return CommandResult(argv, 0, json.dumps(payload), "")
        if "yt-dlp" in name:
            return self._ytdlp(argv, cwd)
        return CommandResult(argv, 1, "", f"unexpected command: {argv}")

    def _ytdlp(self, argv: list[str], cwd: str | Path | None) -> CommandResult:
        url = argv[-1]
        if "--flat-playlist" in argv:
            if url in self.fail_expand:
                return CommandResult(argv, 1, "", "ERROR: cannot expand")
            entries = self.playlist.get(
                url,
                [{"id": "solo", "webpage_url": url, "title": "solo", "duration": 3}],
            )
            stdout = "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n"
            return CommandResult(argv, 0, stdout, "")
        self.downloads.append(url)
        if url in self.fail_urls:
            return CommandResult(argv, 1, "", "ERROR: video unavailable")
        dest = Path(cwd or ".")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "media.mp4").write_bytes(b"fake-video")
        (dest / "media.en.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello\n",
            encoding="utf-8",
        )
        (dest / "media.info.json").write_text(
            json.dumps({"title": f"Title {url}", "duration": 5}),
            encoding="utf-8",
        )
        return CommandResult(argv, 0, "ok\n", "")
