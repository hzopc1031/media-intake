from pathlib import Path

import pytest

from media_intake.errors import InputError
from media_intake.inputs import classify, normalize_url, parse_lines, source_id
from media_intake.models import SourceKind


def test_parse_lines_skips_blank_and_comments() -> None:
    lines = parse_lines(
        [
            "# comment",
            "",
            "  ",
            "https://example.com/a",
            "  ./file.mp4  ",
        ]
    )
    assert lines == ["https://example.com/a", "./file.mp4"]


def test_classify_url_and_extract_from_text() -> None:
    src = classify("请看 https://Example.com/watch?v=1&utm_source=x 谢谢")
    assert src.kind == SourceKind.URL
    assert src.raw == "https://Example.com/watch?v=1&utm_source=x"
    assert src.identity == "https://example.com/watch?v=1"


def test_normalize_url_drops_tracking_keeps_v() -> None:
    assert normalize_url("https://youtu.be/abc?si=zz&feature=share") == "https://youtu.be/abc"


def test_same_url_identity_after_tracking_params() -> None:
    a = classify("https://example.com/v?id=1&utm_campaign=x")
    b = classify("https://example.com/v?id=1")
    assert source_id(a) == source_id(b)


def test_classify_local_uses_resolved_path(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"not-a-real-video")
    src = classify(str(media), cwd=tmp_path)
    assert src.kind == SourceKind.LOCAL
    assert src.identity == str(media.resolve())


def test_reject_localhost_and_private_ip() -> None:
    with pytest.raises(InputError, match="本机"):
        classify("http://localhost/x")
    with pytest.raises(InputError, match="非公网"):
        classify("http://192.168.1.8/v.mp4")
    with pytest.raises(InputError, match="file://"):
        classify("file:///tmp/a.mp4")
