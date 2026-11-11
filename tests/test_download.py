from pathlib import Path

from media_intake.config import BatchOptions
from media_intake.download import build_ytdlp_argv
from media_intake.extractors.ytdlp import build_expand_argv


def _options(tmp_path: Path, **kwargs) -> BatchOptions:
    return BatchOptions(library_root=tmp_path, **kwargs)


def test_download_argv_defaults_and_options(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape\n", encoding="utf-8")
    dest = tmp_path / "item"
    argv = build_ytdlp_argv(
        Path("/usr/bin/yt-dlp"),
        "https://example.com/watch/1",
        dest,
        _options(
            tmp_path,
            format="best[height<=720]",
            audio_only=True,
            write_subs=True,
            sub_langs="zh,en",
            write_auto_subs=True,
            cookies_file=cookies,
            cookies_from_browser="chrome:Default",
            extra_ytdlp_args=["--username", "alice"],
        ),
    )
    assert argv[0].endswith("yt-dlp")
    assert "--ignore-config" in argv
    assert "--no-playlist" in argv
    assert argv[argv.index("-f") + 1] == "best[height<=720]"
    assert "-x" in argv
    assert "--write-subs" in argv
    assert "--convert-subs" in argv
    assert argv[argv.index("--sub-langs") + 1] == "zh,en"
    assert "--write-auto-subs" in argv
    assert argv[argv.index("--cookies") + 1] == str(cookies)
    assert argv[argv.index("--cookies-from-browser") + 1] == "chrome:Default"
    assert "--username" in argv and "alice" in argv
    assert argv[-1] == "https://example.com/watch/1"


def test_no_subs_omits_subtitle_flags(tmp_path: Path) -> None:
    argv = build_ytdlp_argv(
        Path("yt-dlp"),
        "https://example.com/a",
        tmp_path,
        _options(tmp_path, write_subs=False),
    )
    assert "--write-subs" not in argv
    assert "--no-playlist" in argv


def test_expand_argv_includes_limit_and_cookies(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape\n", encoding="utf-8")
    argv = build_expand_argv(
        Path("yt-dlp"),
        "https://example.com/playlist/1",
        _options(
            tmp_path,
            cookies_file=cookies,
            cookies_from_browser="firefox",
        ),
        20,
    )
    assert "--flat-playlist" in argv
    assert "--skip-download" in argv
    assert argv[argv.index("--playlist-end") + 1] == "20"
    assert argv[argv.index("--cookies") + 1] == str(cookies)
    assert argv[argv.index("--cookies-from-browser") + 1] == "firefox"
    assert "--no-playlist" not in argv
    assert argv[-1] == "https://example.com/playlist/1"
