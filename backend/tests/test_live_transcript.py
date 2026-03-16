from app.live_transcript import sanitize_live_transcript_text


def test_sanitize_live_transcript_text_removes_noise_token():
    assert sanitize_live_transcript_text("hello <noise> world") == "hello world"


def test_sanitize_live_transcript_text_removes_noise_token_case_insensitive():
    assert sanitize_live_transcript_text("  < NOISE >  ") == ""


def test_sanitize_live_transcript_text_keeps_regular_text():
    assert sanitize_live_transcript_text("turn on voice mode") == "turn on voice mode"


def test_sanitize_live_transcript_text_preserves_chunk_edge_spacing():
    assert sanitize_live_transcript_text(" text") == " text"
    assert sanitize_live_transcript_text("AI, ") == "AI, "


def test_sanitize_live_transcript_text_collapses_internal_whitespace_only():
    assert sanitize_live_transcript_text("  hello   there  ") == " hello there "
