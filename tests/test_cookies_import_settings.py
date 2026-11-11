from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from media_intake.cli import main
from media_intake.cookies_check import inspect_cookies, parse_netscape_cookies
from media_intake.errors import InputError
from media_intake.importer import import_local_media
from media_intake.config import BatchOptions
from media_intake.settings import load_settings
from tests.fakes import FakeRunner


def _write_netscape(path: Path, rows: list[tuple[str, str, int | None, str]]) -> None:
    lines = ["# Netscape HTTP Cookie File"]
    for domain, name, expires, value in rows:
        expiry = "0" if expires is None else str(expires)
        lines.append(f".{domain}\tTRUE\t/\tTRUE\t{expiry}\t{name}\t{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_parse_and_inspect_cookies_hides_values(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    _write_netscape(
        path,
        [
            ("douyin.com", "sessionid", 2_000_000_000, "secret-douyin-value"),
            ("douyin.com", "ttwid", 2_000_000_000, "device-token"),
            ("xiaohongshu.com", "web_session", 1_000_000_000, "secret-xhs-value"),
            ("xiaohongshu.com", "a1", 2_000_000_000, "device-a1"),
        ],
    )
    metas = parse_netscape_cookies(path)
    assert {c.name for c in metas} == {"sessionid", "ttwid", "web_session", "a1"}
    assert all(not hasattr(c, "value") for c in metas)
    text, reports, code = inspect_cookies(cookies_file=path)
    assert code == 1  # 小红书登录 cookie 已过期
    by_name = {item.platform: item for item in reports}
    assert by_name["抖音"].status == "ok"
    assert by_name["小红书"].status == "expired"
    assert "secret-douyin-value" not in text
    assert "secret-xhs-value" not in text
    assert "device-token" not in text
    assert "device-a1" not in text
    assert "sessionid" in text
    assert "web_session" in text
    assert "不会打印" in text
    assert "media-intake import" in text


def test_inspect_cookies_missing_file(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="找不到 cookies 文件"):
        inspect_cookies(cookies_file=tmp_path / "nope.txt")


def test_cli_cookies_check(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    _write_netscape(path, [("example.com", "foo", 2_000_000_000, "should-not-appear")])
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["cookies", "check", "--cookies", str(path)])
    text = buf.getvalue()
    assert code == 1
    assert "should-not-appear" not in text
    assert "抖音" in text
    assert "小红书" in text


def test_import_local_file_with_source_metadata(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"not-real")
    (tmp_path / "clip.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n导进来的字幕\n",
        encoding="utf-8",
    )
    options = BatchOptions(library_root=tmp_path / "lib", retries=0)
    report = import_local_media(
        media,
        options,
        source_url="https://www.xiaohongshu.com/explore/abc123?utm_source=x",
        title="外部工具下载的笔记",
        uploader="示例作者",
        platform="xiaohongshu",
        upload_date="20261111",
        runner=FakeRunner(),
    )
    assert report.exit_code == 0
    job = report.succeeded[0]
    assert job.source.startswith("https://www.xiaohongshu.com/explore/abc123")
    assert job.source_kind.value == "url"
    assert job.title == "外部工具下载的笔记"
    assert job.uploader == "示例作者"
    assert job.platform == "xiaohongshu"
    assert job.upload_date == "20261111"
    rec = json.loads((options.library_root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["title"] == "外部工具下载的笔记"
    assert rec["platform"] == "xiaohongshu"
    assert rec["uploader"] == "示例作者"
    assert rec["subtitle_path"].endswith("source.srt")
    again = import_local_media(
        media,
        options,
        source_url="https://www.xiaohongshu.com/explore/abc123",
        title="不应覆盖",
        runner=FakeRunner(),
    )
    assert again.skipped
    assert again.skipped[0].title == "外部工具下载的笔记"


def test_cli_import_and_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.conftest import ffmpeg_available, generate_sample_media

    if not ffmpeg_available():
        pytest.skip("需要本机 ffmpeg/ffprobe")
    media = generate_sample_media(tmp_path / "talk.mp4", duration=1.0)
    config = tmp_path / "media-intake.toml"
    library = tmp_path / "from-config"
    config.write_text(
        f'library = "{library.as_posix()}"\nconcurrency = 1\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--config",
            str(config),
            "import",
            str(media),
            "--source-url",
            "https://example.com/v/1",
            "--title",
            "配置导入",
            "--retries",
            "0",
        ]
    )
    assert code == 0
    rec = json.loads((library / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["title"] == "配置导入"
    assert rec["source"] == "https://example.com/v/1"
    assert rec["source_kind"] == "url"


def test_load_settings_merges_and_unknown_keys_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_dir = tmp_path / "xdg" / "media-intake"
    user_dir.mkdir(parents=True)
    (user_dir / "config.toml").write_text(
        'library = "from-user"\nconcurrency = 4\ntoken = "SHOULD-IGNORE"\n',
        encoding="utf-8",
    )
    project = tmp_path / "proj"
    project.mkdir()
    (project / "media-intake.toml").write_text('concurrency = 1\nwhisper_model = "tiny"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(project)
    settings = load_settings()
    assert settings["library"] == "from-user"
    assert settings["concurrency"] == 1
    assert settings["whisper_model"] == "tiny"
    assert "token" not in settings


def test_load_settings_explicit_missing(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="找不到配置文件"):
        load_settings(tmp_path / "no-such.toml")
