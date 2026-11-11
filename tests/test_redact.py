from media_intake.redact import redact_argv


def test_redact_cookies_and_password_values() -> None:
    argv = [
        "yt-dlp",
        "--cookies",
        "/secret/cookies.txt",
        "--password",
        "s3cret",
        "--username=alice",
        "https://example.com/a",
    ]
    redacted = redact_argv(argv)
    assert redacted[2] == "<redacted>"
    assert redacted[4] == "<redacted>"
    assert redacted[5] == "--username=<redacted>"
    assert "s3cret" not in redacted
    assert "cookies.txt" not in " ".join(redacted)
    assert "https://example.com/a" in redacted
