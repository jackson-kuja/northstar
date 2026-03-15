"""Live session management for Gemini Live audio + tool calling."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Awaitable, Callable

from google import genai
from google.genai import types

logger = logging.getLogger("northstar.live")

LIVE_MODEL = os.getenv(
    "LIVE_MODEL",
    "gemini-2.5-flash-native-audio-preview-12-2025",
)

LIVE_SYSTEM_INSTRUCTION = """You are Northstar, a calm and efficient browser assistant.

You are speaking directly with the user in real time.

Rules:
- Speak naturally and concisely.
- Do not expose your reasoning, hidden analysis, or internal plan.
- Do not use markdown, headings, bullets, or asterisk formatting in spoken or transcript output.
- When the user wants the page manipulated, call the browser_task tool with the full goal.
- Do not narrate low-level DOM details unless they help the user.
- When you decide to use browser_task, call it directly instead of announcing that you are about to use it.
- Keep the user's original browser goal active until it is completed, the user changes it, or browser_task says it needs user input.
- Call browser_task at most once for a given goal until that call reaches a terminal status.
- While a browser task is already underway, do not start another browser_task unless the user clearly says to stop, cancel, or switch tasks.
- Treat uncertain background speech or partial chatter during an active task as conversation, not as a new browser command.
- browser_task returns structured fields including status, summary, current_url, current_title, and sometimes retry_goal.
- A browser_task progress update is not a request to call browser_task again.
- Relay updates are authoritative progress from the running browser task. Speak them naturally in one short sentence and do not call browser_task in response.
- While a browser task is running, do not poll browser_task for status. Wait for relayed progress or the final tool result.
- If browser_task returns status=started, acknowledge briefly that work is underway and stay ready for more updates.
- If browser_task returns status=in_progress, give the user a short spoken progress update and do not treat it as final.
- If browser_task returns suppress_user_update=true, produce no speech at all, do not call browser_task again, and wait quietly for the next real user turn or later browser_task result.
- If browser_task returns already_running=true for the same goal, that is an instruction to wait silently. Do not apologize, fill time, or restate that you are checking.
- If browser_task returns status=retry, call browser_task once more with retry_goal if present, otherwise reuse the original goal. Do this before telling the user it failed.
- If browser_task returns status=needs_input, ask the user the question in summary or user_question.
- If browser_task returns status=failed, explain the blocker briefly and mention where the browser ended up if that helps.
- If browser_task returns status=completed, explain what happened in plain language in one short response.
- If browser_task returns status=cancelled, do not continue the old task unless the user asks again.
- For any question about the current page, current tab, visible content, available actions, page state, or what just changed on the page, call browser_task unless the answer is already fully grounded by the most recent browser_task result.
- If the user sends a message that starts with "[[NORTHSTAR_STATUS]]", treat everything after the tag as an internal backend status update.
- Do not say the tag out loud.
- Do not call tools in response to that status update.
- Speak the status update naturally in one short sentence.
"""

ToolCallback = Callable[
    [str, str | None, dict],
    Awaitable[types.FunctionResponse | dict | None],
]
AudioCallback = Callable[[bytes, str], Awaitable[None]]
TranscriptCallback = Callable[[str, str, bool], Awaitable[None]]


class LiveSession:
    """Manages a Gemini Live session and surfaces audio, transcripts, and tool calls."""

    def __init__(
        self,
        client: genai.Client,
        tools: list[types.Tool],
        on_audio: AudioCallback,
        on_transcript: TranscriptCallback,
        on_tool_call: ToolCallback,
    ):
        self.client = client
        self.tools = tools
        self.on_audio = on_audio
        self.on_transcript = on_transcript
        self.on_tool_call = on_tool_call
        self.session = None
        self._running = False
        self._ready = asyncio.Event()
        self._tool_response_lock = asyncio.Lock()

    def is_active(self) -> bool:
        """Return whether the underlying Gemini Live connection is usable."""
        return self._running and self.session is not None

    async def wait_until_ready(self, timeout_seconds: float = 10.0) -> bool:
        """Wait until the live connection is ready for use."""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return False
        return self.is_active()

    def _mark_closed(self):
        self._running = False
        self._ready.clear()
        self.session = None

    @staticmethod
    def _is_normal_close(exc: Exception) -> bool:
        message = str(exc)
        return exc.__class__.__name__ == "ConnectionClosedOK" or "1000 None" in message

    async def start(self):
        """Start the live session and process responses until stopped."""
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=LIVE_SYSTEM_INSTRUCTION,
            tools=self.tools,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=True,
                ),
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
                turn_coverage=types.TurnCoverage.TURN_INCLUDES_ALL_INPUT,
            ),
        )

        try:
            async with self.client.aio.live.connect(
                model=LIVE_MODEL,
                config=config,
            ) as session:
                self.session = session
                self._running = True
                self._ready.set()
                logger.info("Live session started")

                while self._running:
                    received_any_message = False

                    async for msg in session.receive():
                        received_any_message = True
                        if not self._running:
                            break

                        server_content = getattr(msg, "server_content", None)
                        if server_content:
                            if getattr(server_content, "interrupted", False):
                                await self.on_transcript("assistant", "", True)

                            if getattr(server_content, "input_transcription", None):
                                transcript = server_content.input_transcription
                                await self.on_transcript(
                                    "user",
                                    transcript.text or "",
                                    bool(transcript.finished),
                                )

                            if getattr(server_content, "output_transcription", None):
                                transcript = server_content.output_transcription
                                await self.on_transcript(
                                    "assistant",
                                    transcript.text or "",
                                    bool(transcript.finished),
                                )

                            model_turn = getattr(server_content, "model_turn", None)
                            if model_turn:
                                for part in model_turn.parts:
                                    if getattr(part, "inline_data", None):
                                        await self.on_audio(
                                            part.inline_data.data,
                                            part.inline_data.mime_type,
                                        )
                                    if getattr(part, "text", None):
                                        await self.on_transcript("assistant", part.text, True)

                        tool_call = getattr(msg, "tool_call", None)
                        if tool_call:
                            results = []
                            for function_call in tool_call.function_calls:
                                logger.info(
                                    "Live tool call received: name=%s id=%s args=%s",
                                    function_call.name,
                                    getattr(function_call, "id", None),
                                    dict(function_call.args or {}),
                                )
                                call_id = getattr(function_call, "id", None)
                                result = await self.on_tool_call(
                                    function_call.name,
                                    call_id,
                                    dict(function_call.args or {}),
                                )
                                if result is not None:
                                    results.append(result)
                            if results:
                                logger.info("Sending tool responses: %s", results)
                                await self.send_tool_response(results)

                    if not self._running:
                        break
                    if not received_any_message:
                        logger.info("Live session receive stream ended without another turn.")
                        break

        except Exception as exc:
            if self._is_normal_close(exc):
                logger.info("Live session closed cleanly.")
                return
            logger.error("Live session error: %s", exc, exc_info=True)
            raise
        finally:
            self._mark_closed()

    async def _send_realtime_input(self, **kwargs) -> bool:
        if not self.session:
            await self._ready.wait()
        if not self.session:
            return False
        try:
            await self.session.send_realtime_input(**kwargs)
            return True
        except Exception as exc:
            if self._is_normal_close(exc):
                logger.info("Live session input dropped because the connection is already closed.")
            else:
                logger.warning("Live session input failed: %s", exc, exc_info=True)
            self._mark_closed()
            return False

    async def send_audio(
        self,
        audio_data: bytes,
        mime_type: str = "audio/pcm;rate=16000",
    ):
        """Send raw audio input into the live session."""
        return await self._send_realtime_input(
            audio=types.Blob(data=audio_data, mime_type=mime_type)
        )

    async def send_text(self, text: str):
        """Send text input into the live session."""
        return await self._send_realtime_input(text=text)

    async def send_image(self, image_b64: str):
        """Send the latest screenshot into the live session context."""
        image_bytes = base64.b64decode(image_b64)
        return await self._send_realtime_input(
            media=types.Blob(data=image_bytes, mime_type="image/png")
        )

    async def end_audio_turn(self):
        """Signal that the current audio stream has ended."""
        return await self._send_realtime_input(audio_stream_end=True)

    async def send_activity_start(self):
        """Explicitly mark the start of user speech activity."""
        return await self._send_realtime_input(
            activity_start=types.ActivityStart(),
        )

    async def send_activity_end(self):
        """Explicitly mark the end of user speech activity."""
        return await self._send_realtime_input(
            activity_end=types.ActivityEnd(),
        )

    async def stop(self):
        """Stop and close the live session."""
        self._running = False
        if self.session:
            await self.session.close()

    async def send_tool_response(
        self,
        function_responses: types.FunctionResponse | dict | list[types.FunctionResponse | dict],
    ):
        """Send one or more tool responses into the active live session."""
        if not self.session:
            await self._ready.wait()
        if self.session:
            try:
                async with self._tool_response_lock:
                    await self.session.send_tool_response(
                        function_responses=function_responses,
                    )
                return True
            except Exception as exc:
                if self._is_normal_close(exc):
                    logger.info("Live tool response dropped because the connection is already closed.")
                else:
                    logger.warning("Live tool response failed: %s", exc, exc_info=True)
                self._mark_closed()
        return False
