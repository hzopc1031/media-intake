from pathlib import Path

from media_intake.subtitles import find_sidecar_subtitles, subtitle_to_text, write_plain_text


def test_srt_to_text_strips_index_and_timestamps() -> None:
    srt = (
        "1\n"
        "00:00:00,000 --> 00:00:01,500\n"
        "第一句\n"
        "\n"
        "2\n"
        "00:00:01,500 --> 00:00:03,000\n"
        "第二句\n"
    )
    assert subtitle_to_text(srt) == "第一句\n\n第二句\n"


def test_vtt_to_text_strips_header_and_tags() -> None:
    vtt = (
        "WEBVTT\n"
        "\n"
        "00:00:00.000 --> 00:00:01.000\n"
        "<c>Hello</c> world\n"
    )
    assert subtitle_to_text(vtt) == "Hello world\n"


def test_write_plain_text_and_sidecar_discovery(tmp_path: Path) -> None:
    media = tmp_path / "talk.mp4"
    media.write_bytes(b"x")
    srt = tmp_path / "talk.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
    zh = tmp_path / "talk.zh.srt"
    zh.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    found = find_sidecar_subtitles(media)
    assert {p.name for p in found} == {"talk.srt", "talk.zh.srt"}
    dest = tmp_path / "out.txt"
    write_plain_text(srt, dest)
    assert dest.read_text(encoding="utf-8") == "Hi\n"
