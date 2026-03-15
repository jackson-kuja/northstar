"""Tests for Gemini Live session resilience."""

import asyncio
from types import SimpleNamespace

from app.live_session import LiveSession


class ConnectionClosedOK(Exception):
    pass


class ClosedSession:
    async def send_realtime_input(self, **kwargs):
        raise ConnectionClosedOK()

    async def send_tool_response(self, **kwargs):
        raise ConnectionClosedOK()


async def _noop_audio(data, mime_type):
    return None


async def _noop_transcript(role, text, finished):
    return None


async def _noop_tool_call(name, call_id, args):
    return None


class FakeSession:
    def __init__(self, turns):
        self.turns = list(turns)
        self.receive_calls = 0
        self.closed = False

    def receive(self):
        async def iterator():
            if self.receive_calls >= len(self.turns):
                return
            turn = self.turns[self.receive_calls]
            self.receive_calls += 1
            for message in turn:
                yield message

        return iterator()

    async def send_realtime_input(self, **kwargs):
        return None

    async def send_tool_response(self, **kwargs):
        return None

    async def close(self):
        self.closed = True


class FakeConnectContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeClient:
    def __init__(self, session):
        self.aio = SimpleNamespace(
            live=SimpleNamespace(
                connect=lambda **kwargs: FakeConnectContext(session)
            )
        )


def make_server_content_message(text):
    transcript = SimpleNamespace(text=text, finished=True)
    return SimpleNamespace(
        server_content=SimpleNamespace(
            interrupted=False,
            input_transcription=None,
            output_transcription=transcript,
            model_turn=None,
        ),
        tool_call=None,
    )


def test_live_session_ignores_audio_after_connection_closes():
    live = LiveSession(
        client=None,
        tools=[],
        on_audio=_noop_audio,
        on_transcript=_noop_transcript,
        on_tool_call=_noop_tool_call,
    )
    live.session = ClosedSession()
    live._running = True
    live._ready.set()

    sent = asyncio.run(live.send_audio(b"abc"))

    assert sent is False
    assert live.is_active() is False


def test_live_session_ignores_tool_response_after_connection_closes():
    live = LiveSession(
        client=None,
        tools=[],
        on_audio=_noop_audio,
        on_transcript=_noop_transcript,
        on_tool_call=_noop_tool_call,
    )
    live.session = ClosedSession()
    live._running = True
    live._ready.set()

    sent = asyncio.run(live.send_tool_response({"ok": True}))

    assert sent is False
    assert live.is_active() is False


def test_live_session_processes_multiple_turns_before_stopping():
    seen_transcripts = []
    fake_session = FakeSession(
        [
            [make_server_content_message("First turn")],
            [make_server_content_message("Second turn")],
        ]
    )

    async def on_transcript(role, text, finished):
        seen_transcripts.append((role, text, finished))

    live = LiveSession(
        client=FakeClient(fake_session),
        tools=[],
        on_audio=_noop_audio,
        on_transcript=on_transcript,
        on_tool_call=_noop_tool_call,
    )

    asyncio.run(live.start())

    assert fake_session.receive_calls == 2
    assert seen_transcripts == [
        ("assistant", "First turn", True),
        ("assistant", "Second turn", True),
    ]
