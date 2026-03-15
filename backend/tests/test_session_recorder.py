"""Tests for local session recording."""

import asyncio
import base64
import json

from app.session_recorder import (
    SessionRecorder,
    list_recorded_sessions,
    load_session_meta,
    read_session_events,
)


def test_session_recorder_persists_events_and_artifacts(tmp_path):
    recorder = SessionRecorder(
        "session-123",
        root=tmp_path,
        enabled=True,
        store_audio=False,
    )

    asyncio.run(
        recorder.log_event(
            source="extension",
            event_type="page_state_received",
            payload={"url": "https://example.com"},
            json_artifacts={
                "page_state": {
                    "url": "https://example.com",
                    "title": "Example",
                }
            },
            base64_artifacts={
                "screenshot": {
                    "data": base64.b64encode(b"png-bytes").decode("ascii"),
                    "mime_type": "image/png",
                }
            },
        )
    )
    asyncio.run(recorder.close(status="complete"))

    meta = load_session_meta("session-123", root=tmp_path)
    events = read_session_events("session-123", root=tmp_path)
    sessions = list_recorded_sessions(root=tmp_path)

    assert meta is not None
    assert meta["status"] == "complete"
    assert meta["event_count"] == 1
    assert len(events) == 1
    assert events[0]["source"] == "extension"
    assert events[0]["type"] == "page_state_received"
    assert len(events[0]["artifacts"]) == 2
    assert sessions[0]["session_id"] == "session-123"

    page_state_path = tmp_path / "session-123" / "artifacts" / "page_state" / "00001_page_state.json"
    screenshot_path = tmp_path / "session-123" / "artifacts" / "screenshot" / "00001_screenshot.png"

    assert page_state_path.exists()
    assert screenshot_path.exists()
    assert json.loads(page_state_path.read_text(encoding="utf-8"))["title"] == "Example"
    assert screenshot_path.read_bytes() == b"png-bytes"
