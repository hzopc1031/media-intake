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


from media_intake.redact import format_redacted_command, redact_error_text


def test_redact_error_text_strips_url_query() -> None:
    text = "ERROR: Unsupported URL: https://webcast.example.com/reflow/1?iid=should-not-leak&foo=bar"
    cleaned = redact_error_text(text)
    assert "iid=" not in cleaned
    assert cleaned.endswith("https://webcast.example.com/reflow/1")
    cmd = format_redacted_command(["/opt/venv/bin/yt-dlp", "--cookies", "/secret/c.txt", "https://example.com/a?x=1"])
    assert cmd.startswith("yt-dlp ")
    assert "/opt/venv" not in cmd
    assert "/secret" not in cmd
    assert cmd.endswith("<url>")
