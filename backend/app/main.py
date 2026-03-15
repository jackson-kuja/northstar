"""Northstar backend for Gemini Live + Pro-orchestrated browser control."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.browser_agent import BrowserAgent
from app.live_session import LiveSession
from app.session_recorder import (
    DEFAULT_RECORDINGS_ROOT,
    LOCAL_SESSION_RECORDING_ENABLED,
    SessionRecorder,
    list_recorded_sessions,
    load_session_meta,
    read_session_events,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("northstar")

load_dotenv(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
    override=True,
)

try:
    from google.cloud import firestore

    db = firestore.AsyncClient(project=os.getenv("GCP_PROJECT", "northstar-agent"))
    HAS_FIRESTORE = True
    logger.info("Firestore client initialized")
except Exception as exc:  # pragma: no cover - depends on runtime env
    db = None
    HAS_FIRESTORE = False
    logger.warning("Firestore unavailable, using in-memory sessions: %s", exc)

try:
    import google.cloud.logging

    cloud_logging_client = google.cloud.logging.Client()
    cloud_logging_client.setup_logging()
    logger.info("Cloud Logging attached")
except Exception:  # pragma: no cover - depends on runtime env
    logger.info("Cloud Logging unavailable, using stdout")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else genai.Client()
browser_agent = BrowserAgent(client)
RECENT_BROWSER_RESULT_TTL_SECONDS = float(
    os.getenv("RECENT_BROWSER_RESULT_TTL_SECONDS", "8")
)
BROWSER_PROGRESS_MIN_SPEAK_INTERVAL_SECONDS = float(
    os.getenv("BROWSER_PROGRESS_MIN_SPEAK_INTERVAL_SECONDS", "8")
)
INITIAL_BROWSER_PROGRESS_MESSAGE = "I'm reviewing the page and planning the first step."
BROWSER_PROGRESS_HEARTBEAT_MESSAGES = {
    "I'm still working through the page.",
    "Still here. I'm deciding the next browser action.",
}
INTERNAL_STATUS_UPDATE_TAG = "[[NORTHSTAR_STATUS]]"

LIVE_TOOL_DECLARATIONS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="browser_task",
                description=(
                    "Complete or advance a browser task on the current page. "
                    "Returns structured status fields including status, summary, "
                    "current_url, current_title, and optional retry_goal."
                ),
                behavior=types.Behavior.NON_BLOCKING,
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "goal": types.Schema(
                            type="STRING",
                            description="The browser task the user wants completed.",
                        )
                    },
                    required=["goal"],
                ),
            )
        ]
    )
]

sessions: dict[str, dict[str, Any]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Northstar backend starting")
    yield
    logger.info("Northstar backend shutting down")


app = FastAPI(title="Northstar", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
DEFAULT_RECORDINGS_ROOT.mkdir(parents=True, exist_ok=True)
app.mount(
    "/debug/files",
    StaticFiles(directory=str(DEFAULT_RECORDINGS_ROOT)),
    name="session_debug_files",
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "firestore": HAS_FIRESTORE,
        "local_session_recording": {
            "enabled": LOCAL_SESSION_RECORDING_ENABLED,
            "root": str(DEFAULT_RECORDINGS_ROOT),
        },
    }


@app.get("/debug/sessions")
async def debug_sessions(limit: int = Query(default=25, ge=1, le=200)):
    return {
        "root": str(DEFAULT_RECORDINGS_ROOT),
        "sessions": list_recorded_sessions(limit=limit),
    }


@app.get("/debug/sessions/{session_id}")
async def debug_session(
    session_id: str,
    event_limit: int = Query(default=100, ge=1, le=1000),
):
    meta = load_session_meta(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Unknown session")

    return {
        "session": meta,
        "events": read_session_events(session_id, limit=event_limit),
    }


@app.get("/debug/sessions/{session_id}/events")
async def debug_session_events(
    session_id: str,
    limit: int = Query(default=200, ge=1, le=5000),
    after_seq: int = Query(default=0, ge=0),
):
    meta = load_session_meta(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Unknown session")

    return {
        "session": meta,
        "events": read_session_events(
            session_id,
            limit=limit,
            after_seq=after_seq,
        ),
    }


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    logger.info("Session %s connected", session_id)

    session = create_session(session_id, websocket)
    sessions[session_id] = session
    await record_session_event(
        session,
        source="system",
        event_type="session_connected",
        payload={"transport": "websocket"},
    )

    if HAS_FIRESTORE:
        try:
            await db.collection("sessions").document(session_id).set(
                {"created": firestore.SERVER_TIMESTAMP, "status": "active"}
            )
        except Exception as exc:  # pragma: no cover - depends on runtime env
            logger.warning("Firestore session create failed: %s", exc)

    try:
        while True:
            raw = await websocket.receive_text()
            message = json.loads(raw)
            await handle_client_message(session, message)
    except WebSocketDisconnect:
        logger.info("Session %s disconnected", session_id)
    except Exception as exc:
        logger.error("Session %s error: %s", session_id, exc, exc_info=True)
    finally:
        await cleanup_session(session_id)


def create_session(session_id: str, websocket: WebSocket) -> dict[str, Any]:
    return {
        "id": session_id,
        "websocket": websocket,
        "send_lock": asyncio.Lock(),
        "recorder": (
            SessionRecorder(session_id)
            if LOCAL_SESSION_RECORDING_ENABLED
            else None
        ),
        "page_state": None,
        "last_screenshot": None,
        "last_screenshot_payload": None,
        "live_session": None,
        "live_task": None,
        "browser_task_job": None,
        "current_browser_goal": None,
        "current_browser_goal_normalized": "",
        "last_browser_progress_message": "",
        "last_browser_result": None,
        "last_browser_result_at": 0.0,
        "last_browser_goal_normalized": "",
        "duplicate_browser_task_call_count": 0,
        "last_relayed_browser_progress_message": "",
        "last_relayed_browser_progress_at": 0.0,
        "last_user_input_at": 0.0,
        "user_turn_active": False,
        "last_assistant_output_at": 0.0,
        "suppress_live_assistant_output": False,
        "pending_action_future": None,
    }


async def cleanup_session(session_id: str):
    session = sessions.pop(session_id, None)
    if not session:
        return

    await record_session_event(
        session,
        source="system",
        event_type="session_cleanup_started",
        payload={},
    )

    live_session = session.get("live_session")
    if live_session:
        try:
            await live_session.stop()
        except Exception:
            pass

    live_task = session.get("live_task")
    if live_task:
        live_task.cancel()
        try:
            await live_task
        except BaseException:
            pass

    pending_action = session.get("pending_action_future")
    if pending_action and not pending_action.done():
        pending_action.cancel()

    browser_task_job = session.get("browser_task_job")
    if browser_task_job and not browser_task_job.done():
        browser_task_job.cancel()

    if HAS_FIRESTORE:
        try:
            await db.collection("sessions").document(session_id).set(
                {"status": "disconnected"}, merge=True
            )
        except Exception:
            pass

    recorder = session.get("recorder")
    if recorder:
        await recorder.close(status="disconnected")


async def handle_client_message(session: dict[str, Any], message: dict[str, Any]):
    msg_type = message.get("type", "")

    if msg_type == "page_state":
        page_state = message.get("data", {})
        session["page_state"] = page_state
        await record_session_event(
            session,
            source="extension",
            event_type="page_state_received",
            payload=_summarize_page_state(page_state),
            json_artifacts={"page_state": page_state},
        )
        await send_json(session, {"type": "status", "data": {"state": "page_received"}})
        return

    if msg_type == "screenshot":
        screenshot = message.get("data")
        if isinstance(screenshot, dict):
            session["last_screenshot_payload"] = screenshot
            session["last_screenshot"] = screenshot.get("data")
            await record_session_event(
                session,
                source="extension",
                event_type="screenshot_received",
                payload=_summarize_screenshot(screenshot),
                base64_artifacts={"screenshot": screenshot},
            )
        return

    if msg_type == "live_start":
        await record_session_event(
            session,
            source="extension",
            event_type="live_start_requested",
            payload={},
        )
        await ensure_live_session(session)
        return

    if msg_type == "live_audio_chunk":
        live_session = session.get("live_session")
        if not live_session or not live_session.is_active():
            await ensure_live_session(session)
            live_session = session.get("live_session")
            if not live_session or not live_session.is_active():
                return
        chunk = message.get("data") or ""
        mime_type = message.get("mimeType", "audio/pcm;rate=16000")
        if chunk:
            await record_session_event(
                session,
                source="user",
                event_type="live_audio_input",
                payload={
                    "mime_type": mime_type,
                    "size_bytes": _estimate_base64_size(str(chunk)),
                },
                base64_artifacts=(
                    {"user_audio": {"data": chunk, "mime_type": mime_type}}
                    if _should_store_audio(session)
                    else None
                ),
            )
            await live_session.send_audio(
                data_from_base64(chunk),
                mime_type=mime_type,
            )
        return

    if msg_type == "live_activity_start":
        live_session = session.get("live_session")
        if not live_session or not live_session.is_active():
            await ensure_live_session(session)
            live_session = session.get("live_session")
        if live_session and live_session.is_active():
            session["suppress_live_assistant_output"] = False
            session["user_turn_active"] = True
            session["last_user_input_at"] = time.monotonic()
            await record_session_event(
                session,
                source="user",
                event_type="live_activity_start",
                payload={},
            )
            await live_session.send_activity_start()
        return

    if msg_type == "live_activity_end":
        live_session = session.get("live_session")
        if live_session and live_session.is_active():
            session["user_turn_active"] = False
            await record_session_event(
                session,
                source="user",
                event_type="live_activity_end",
                payload={},
            )
            await live_session.send_activity_end()
        return

    if msg_type == "live_end":
        live_session = session.get("live_session")
        if live_session and live_session.is_active():
            await record_session_event(
                session,
                source="user",
                event_type="live_turn_ended",
                payload={},
            )
            await live_session.end_audio_turn()
        return

    if msg_type == "live_stop":
        await record_session_event(
            session,
            source="user",
            event_type="live_session_stop_requested",
            payload={},
        )
        live_session = session.get("live_session")
        session["suppress_live_assistant_output"] = True
        session["live_session"] = None
        browser_task_job = session.get("browser_task_job")
        if browser_task_job and not browser_task_job.done():
            browser_task_job.cancel()
        if live_session:
            try:
                await live_session.stop()
            except Exception:
                pass
        await send_json(session, {"type": "status", "data": {"state": "connected"}})
        return

    if msg_type == "user_message":
        await ensure_live_session(session)
        text = message.get("text", "").strip()
        if text:
            session["suppress_live_assistant_output"] = False
            session["last_user_input_at"] = time.monotonic()
            await record_session_event(
                session,
                source="user",
                event_type="user_message",
                payload={"text": text},
            )
            live_session = session.get("live_session")
            if live_session and not await live_session.send_text(text):
                await ensure_live_session(session, force=True)
                live_session = session.get("live_session")
                if live_session:
                    await live_session.send_text(text)
        return

    if msg_type == "action_result":
        result = message.get("data", {})
        await record_session_event(
            session,
            source="extension",
            event_type="action_result_received",
            payload=_summarize_action_result(result),
            json_artifacts=(
                {"page_state": result.get("page_state")}
                if result.get("page_state")
                else None
            ),
            base64_artifacts=(
                {"screenshot": result.get("screenshot")}
                if isinstance(result.get("screenshot"), dict)
                else None
            ),
        )
        future = session.get("pending_action_future")
        if future and not future.done():
            future.set_result(result)
        return

    await record_session_event(
        session,
        source="extension",
        event_type="unhandled_client_message",
        payload={"message_type": msg_type, "payload": _safe_message_payload(message)},
    )

async def ensure_live_session(session: dict[str, Any], force: bool = False):
    existing_live_session = session.get("live_session")
    if existing_live_session and existing_live_session.is_active() and not force:
        return

    async def on_audio(data: bytes, mime_type: str):
        if (
            session.get("suppress_live_assistant_output")
            and session.get("browser_task_job")
            and not session["browser_task_job"].done()
        ):
            return
        session["last_assistant_output_at"] = time.monotonic()
        await record_session_event(
            session,
            source="live",
            event_type="assistant_audio_output",
            payload={"mime_type": mime_type, "size_bytes": len(data)},
            blob_artifacts=(
                {"assistant_audio": {"data": data, "mime_type": mime_type}}
                if _should_store_audio(session)
                else None
            ),
        )
        await send_json(
            session,
            {
                "type": "live_audio_output",
                "data": base64_from_bytes(data),
                "mimeType": mime_type,
            },
        )

    async def on_transcript(role: str, text: str, finished: bool):
        text = str(text or "")
        if not text and not finished:
            return
        if role == "user" and text:
            session["last_user_input_at"] = time.monotonic()
        suppress_assistant_output = (
            role == "assistant"
            and session.get("suppress_live_assistant_output")
            and session.get("browser_task_job")
            and not session["browser_task_job"].done()
        )
        if suppress_assistant_output and text:
            await record_session_event(
                session,
                source="live",
                event_type="transcript_suppressed",
                payload={"role": role, "text": text, "finished": finished},
            )
            if not finished:
                return
            # Preserve transcript turn boundaries in the UI even when text is suppressed.
            text = ""
        if role == "assistant" and text:
            session["last_assistant_output_at"] = time.monotonic()
        await record_session_event(
            session,
            source="live",
            event_type="transcript",
            payload={"role": role, "text": text, "finished": finished},
        )
        await send_json(
            session,
            {
                "type": "live_transcript",
                "role": role,
                "text": text,
                "finished": finished,
            },
        )

    async def on_tool_call(name: str, call_id: str | None, args: dict[str, Any]):
        await record_session_event(
            session,
            source="live",
            event_type="tool_call_received",
            payload={"name": name, "call_id": call_id, "args": args},
        )
        if name != "browser_task":
            response = types.FunctionResponse(
                id=call_id,
                name=name,
                response={
                    "success": False,
                    "status": "failed",
                    "summary": f"Unsupported tool call: {name}",
                    "mode": "orchestrator",
                },
            )
            await record_session_event(
                session,
                source="backend",
                event_type="tool_response_prepared",
                payload=_summarize_function_response(response),
            )
            return response
        goal = str(args.get("goal", "")).strip()
        normalized_goal = normalize_browser_goal(goal)
        recent_result = session.get("last_browser_result")
        recent_result_at = float(session.get("last_browser_result_at") or 0.0)
        recent_goal = session.get("last_browser_goal_normalized", "")
        if (
            goal
            and recent_result
            and normalized_goal
            and normalized_goal == recent_goal
            and (time.monotonic() - recent_result_at) <= RECENT_BROWSER_RESULT_TTL_SECONDS
        ):
            response = types.FunctionResponse(
                id=call_id,
                name=name,
                response={
                    **recent_result,
                    "cached": True,
                    "suppress_user_update": True,
                },
                will_continue=False,
                scheduling=types.FunctionResponseScheduling.WHEN_IDLE,
            )
            await record_session_event(
                session,
                source="backend",
                event_type="tool_response_prepared",
                payload=_summarize_function_response(response),
            )
            return response

        job = session.get("browser_task_job")
        if job and not job.done():
            active_goal = session.get("current_browser_goal", "").strip()
            if goal and active_goal and goal.casefold() == active_goal.casefold():
                duplicate_count = int(session.get("duplicate_browser_task_call_count") or 0) + 1
                session["duplicate_browser_task_call_count"] = duplicate_count
                if duplicate_count == 3 or duplicate_count % 5 == 0:
                    logger.warning(
                        "Gemini Live re-called browser_task %s times for active goal=%r",
                        duplicate_count,
                        active_goal,
                    )
                progress_summary = (
                    session.get("last_browser_progress_message")
                    or f"I'm still working on {active_goal}."
                )
                response = types.FunctionResponse(
                    id=call_id,
                    name=name,
                    response={
                        "status": "in_progress",
                        "summary": progress_summary,
                        "goal": active_goal,
                        "suppress_user_update": True,
                        "already_running": True,
                    },
                    will_continue=False,
                    scheduling=types.FunctionResponseScheduling.SILENT,
                )
                await record_session_event(
                    session,
                    source="backend",
                    event_type="tool_response_prepared",
                    payload=_summarize_function_response(response),
                )
                return response

            switch_message = (
                f"I'm already working on {active_goal}. "
                "If you want to switch tasks, say stop and then ask again."
                if active_goal
                else "I'm already working on another browser task. "
                "If you want to switch tasks, say stop and then ask again."
            )
            response = types.FunctionResponse(
                id=call_id,
                name=name,
                response={
                    "status": "needs_input",
                    "summary": switch_message,
                    "goal": active_goal or goal,
                    "user_question": switch_message,
                },
                will_continue=False,
                scheduling=types.FunctionResponseScheduling.INTERRUPT,
            )
            await record_session_event(
                session,
                source="backend",
                event_type="tool_response_prepared",
                payload=_summarize_function_response(response),
            )
            return response

        session["browser_task_job"] = asyncio.create_task(
            run_browser_task_job(session, call_id, goal)
        )
        session["current_browser_goal_normalized"] = normalized_goal

        response = types.FunctionResponse(
            id=call_id,
            name=name,
            response={
                "status": "started",
                "summary": "Northstar is taking over the browser now.",
                "goal": goal,
            },
            will_continue=True,
            scheduling=types.FunctionResponseScheduling.WHEN_IDLE,
        )
        await record_session_event(
            session,
            source="backend",
            event_type="tool_response_prepared",
            payload=_summarize_function_response(response),
        )
        return response

    live_session = LiveSession(
        client=client,
        tools=LIVE_TOOL_DECLARATIONS,
        on_audio=on_audio,
        on_transcript=on_transcript,
        on_tool_call=on_tool_call,
    )
    session["live_session"] = live_session

    async def runner():
        try:
            await record_session_event(
                session,
                source="live",
                event_type="session_starting",
                payload={},
            )
            await live_session.start()
        except Exception as exc:
            await record_session_event(
                session,
                source="live",
                event_type="session_error",
                payload={"error": str(exc)},
            )
            try:
                await send_json(
                    session,
                    {
                        "type": "assistant_message",
                        "text": f"Live session error: {exc}",
                    },
                )
                await send_json(
                    session,
                    {"type": "status", "data": {"state": "error", "message": "Live session failed"}},
                )
            except RuntimeError:
                logger.info("WebSocket already closed while reporting live session error.")
        finally:
            await record_session_event(
                session,
                source="live",
                event_type="session_stopped",
                payload={},
            )
            if session.get("live_session") is live_session:
                session["live_session"] = None
            if session.get("live_task") is asyncio.current_task():
                session["live_task"] = None

    session["live_task"] = asyncio.create_task(runner())
    if await live_session.wait_until_ready():
        await record_session_event(
            session,
            source="live",
            event_type="session_ready",
            payload={},
        )
        await send_json(session, {"type": "status", "data": {"state": "connected"}})
    else:
        await record_session_event(
            session,
            source="live",
            event_type="session_ready_timeout",
            payload={},
        )


async def close_active_assistant_transcript(
    session: dict[str, Any],
    *,
    reason: str,
) -> None:
    await record_session_event(
        session,
        source="backend",
        event_type="assistant_turn_boundary_injected",
        payload={"reason": reason},
    )
    await send_json(
        session,
        {
            "type": "live_transcript",
            "role": "assistant",
            "text": "",
            "finished": True,
        },
    )


async def relay_update_via_live(
    session: dict[str, Any],
    text: str,
    *,
    reason: str,
    since_timestamp: float | None = None,
) -> bool:
    text = str(text or "").strip()
    if not text:
        return False
    if since_timestamp is not None and _should_skip_spoken_update(
        session,
        since_timestamp=since_timestamp,
    ):
        return False

    live_session = session.get("live_session")
    if not live_session or not live_session.is_active():
        await ensure_live_session(session, force=True)
        live_session = session.get("live_session")
        if live_session and not live_session.is_active():
            await live_session.wait_until_ready()

    if not live_session or not live_session.is_active():
        return False

    logger.info("Relaying browser-task update back through Gemini Live.")
    await record_session_event(
        session,
        source="backend",
        event_type="live_relay_message",
        payload={"text": text},
    )
    await close_active_assistant_transcript(session, reason=reason)
    return await live_session.send_text(f"{INTERNAL_STATUS_UPDATE_TAG} {text}")


async def run_browser_task_job(
    session: dict[str, Any],
    call_id: str | None,
    goal: str,
):
    progress_message = ""
    session["suppress_live_assistant_output"] = False
    session["current_browser_goal"] = goal
    session["current_browser_goal_normalized"] = normalize_browser_goal(goal)
    session["last_browser_progress_message"] = ""
    session["duplicate_browser_task_call_count"] = 0
    session["last_relayed_browser_progress_message"] = ""
    session["last_relayed_browser_progress_at"] = 0.0
    await record_session_event(
        session,
        source="browser_task",
        event_type="job_started",
        payload={"goal": goal, "call_id": call_id},
    )

    async def send_status(data: dict[str, Any]):
        await record_session_event(
            session,
            source="browser_task",
            event_type="status_update",
            payload=data,
        )
        await send_json(session, {"type": "browser_task_status", "data": data})

    async def send_progress(message: str):
        nonlocal progress_message
        message = str(message or "").strip()
        if not message or message == progress_message:
            return
        progress_generated_at = time.monotonic()
        progress_message = message
        session["last_browser_progress_message"] = message
        await record_session_event(
            session,
            source="browser_task",
            event_type="progress_update",
            payload={"message": message, "goal": goal},
        )
        await send_status(
            {
                "phase": "orchestrator",
                "message": message,
            }
        )
        live_session = session.get("live_session")
        if live_session and live_session.is_active() and call_id:
            tool_response = types.FunctionResponse(
                id=call_id,
                name="browser_task",
                response={
                    "status": "in_progress",
                    "summary": message,
                    "goal": goal,
                },
                will_continue=True,
                scheduling=types.FunctionResponseScheduling.SILENT,
            )
            await record_session_event(
                session,
                source="backend",
                event_type="tool_response_sent",
                payload=_summarize_function_response(tool_response),
            )
            await live_session.send_tool_response(tool_response)
        if _should_relay_browser_progress(session, message):
            relay_sent = await relay_update_via_live(
                session,
                message,
                reason="browser_progress",
                since_timestamp=progress_generated_at,
            )
            if relay_sent:
                session["last_relayed_browser_progress_message"] = message
                session["last_relayed_browser_progress_at"] = time.monotonic()

    try:
        result = await browser_agent.run_task(
            goal=goal,
            session=session,
            execute_action=lambda action: execute_browser_action(session, action),
            send_status=send_status,
            send_progress=send_progress,
        )
    except asyncio.CancelledError:
        live_session = session.get("live_session")
        if live_session and call_id:
            tool_response = types.FunctionResponse(
                id=call_id,
                name="browser_task",
                response={
                    "status": "cancelled",
                    "summary": "Northstar stopped the previous browser task.",
                    "goal": goal,
                },
                will_continue=False,
                scheduling=types.FunctionResponseScheduling.INTERRUPT,
            )
            await record_session_event(
                session,
                source="backend",
                event_type="tool_response_sent",
                payload=_summarize_function_response(tool_response),
            )
            await live_session.send_tool_response(tool_response)
        await record_session_event(
            session,
            source="browser_task",
            event_type="job_cancelled",
            payload={"goal": goal},
        )
        raise
    except Exception as exc:
        logger.error("Browser task failed unexpectedly: %s", exc, exc_info=True)
        await record_session_event(
            session,
            source="browser_task",
            event_type="job_error",
            payload={"goal": goal, "error": str(exc)},
        )
        page_state = session.get("page_state") or {}
        result = {
            "success": False,
            "status": "failed",
            "summary": "Northstar hit an internal browser-task error. Please try again.",
            "mode": "orchestrator",
            "goal": goal,
            "recoverable": False,
            "current_url": page_state.get("url", ""),
            "current_title": page_state.get("title", ""),
        }
    finally:
        if session.get("browser_task_job") is asyncio.current_task():
            session["browser_task_job"] = None
            session["current_browser_goal"] = None
            session["current_browser_goal_normalized"] = ""

    live_session = session.get("live_session")
    session["suppress_live_assistant_output"] = False
    session["last_browser_result"] = dict(result)
    session["last_browser_result_at"] = time.monotonic()
    session["last_browser_goal_normalized"] = normalize_browser_goal(goal)
    await send_status(
        {
            "phase": "browser_task",
            "status": result.get("status", ""),
            "message": result.get("summary", ""),
        }
    )
    await record_session_event(
        session,
        source="browser_task",
        event_type="job_finished",
        payload=result,
    )
    fallback_text = str(result.get("summary", "")).strip()
    tool_response_sent_at = time.monotonic()
    if live_session and live_session.is_active() and call_id:
        terminal_scheduling = (
            types.FunctionResponseScheduling.SILENT
            if _should_skip_spoken_update(
                session,
                since_timestamp=tool_response_sent_at,
            )
            else types.FunctionResponseScheduling.INTERRUPT
        )
        tool_response = types.FunctionResponse(
            id=call_id,
            name="browser_task",
            response=result,
            will_continue=False,
            scheduling=terminal_scheduling,
        )
        await record_session_event(
            session,
            source="backend",
            event_type="tool_response_sent",
            payload=_summarize_function_response(tool_response),
        )
        await live_session.send_tool_response(tool_response)
    if fallback_text:
        await asyncio.sleep(1.2)
        if session.get("last_assistant_output_at", 0.0) < tool_response_sent_at:
            relay_sent = await relay_update_via_live(
                session,
                fallback_text,
                reason="browser_result_fallback",
                since_timestamp=tool_response_sent_at,
            )
            if relay_sent:
                relay_started_at = time.monotonic()
                await asyncio.sleep(1.2)
                if session.get("last_assistant_output_at", 0.0) >= relay_started_at:
                    return
            await send_json(
                session,
                {
                    "type": "assistant_message",
                    "text": fallback_text,
                },
            )


async def execute_browser_action(
    session: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    session["pending_action_future"] = future
    await record_session_event(
        session,
        source="backend",
        event_type="browser_action_dispatched",
        payload=action,
    )
    await send_json(session, {"type": "action", "data": action})

    try:
        result = await asyncio.wait_for(future, timeout=30)
    finally:
        if session.get("pending_action_future") is future:
            session["pending_action_future"] = None

    page_state = result.get("page_state")
    if page_state:
        session["page_state"] = page_state

    screenshot = result.get("screenshot")
    if screenshot:
        session["last_screenshot_payload"] = screenshot
        session["last_screenshot"] = screenshot.get("data")

    await record_session_event(
        session,
        source="backend",
        event_type="browser_action_completed",
        payload=_summarize_action_result(result),
        json_artifacts=(
            {"page_state": page_state}
            if page_state
            else None
        ),
        base64_artifacts=(
            {"screenshot": screenshot}
            if isinstance(screenshot, dict)
            else None
        ),
    )
    return result


async def send_json(session: dict[str, Any], message: dict[str, Any]):
    async with session["send_lock"]:
        try:
            await session["websocket"].send_json(message)
        except RuntimeError:
            logger.info("Skipping websocket send because the client is already closed.")


def base64_from_bytes(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def data_from_base64(data: str) -> bytes:
    import base64

    return base64.b64decode(data)


def normalize_browser_goal(goal: str) -> str:
    return " ".join(str(goal or "").casefold().split())


def _should_relay_browser_progress(session: dict[str, Any], message: str) -> bool:
    normalized_message = normalize_browser_goal(message)
    if not normalized_message:
        return False

    if normalized_message == normalize_browser_goal(INITIAL_BROWSER_PROGRESS_MESSAGE):
        return False

    previous_message = str(session.get("last_relayed_browser_progress_message") or "")
    if normalized_message == normalize_browser_goal(previous_message):
        return False

    if normalized_message not in {
        normalize_browser_goal(item) for item in BROWSER_PROGRESS_HEARTBEAT_MESSAGES
    }:
        return True

    last_relayed_at = float(session.get("last_relayed_browser_progress_at") or 0.0)
    return (time.monotonic() - last_relayed_at) >= BROWSER_PROGRESS_MIN_SPEAK_INTERVAL_SECONDS


def _should_skip_spoken_update(
    session: dict[str, Any],
    *,
    since_timestamp: float,
) -> bool:
    if bool(session.get("user_turn_active")):
        return True
    return float(session.get("last_user_input_at") or 0.0) > since_timestamp


async def record_session_event(
    session: dict[str, Any],
    *,
    source: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    json_artifacts: dict[str, Any] | None = None,
    base64_artifacts: dict[str, dict[str, Any]] | None = None,
    blob_artifacts: dict[str, dict[str, Any]] | None = None,
):
    recorder = session.get("recorder")
    if not recorder:
        return
    try:
        await recorder.log_event(
            source=source,
            event_type=event_type,
            payload=payload,
            json_artifacts=json_artifacts,
            base64_artifacts=base64_artifacts,
            blob_artifacts=blob_artifacts,
        )
    except Exception as exc:
        logger.warning("Session recording failed for %s: %s", event_type, exc)


def _should_store_audio(session: dict[str, Any]) -> bool:
    recorder = session.get("recorder")
    return bool(recorder and getattr(recorder, "store_audio", False))


def _estimate_base64_size(data: str) -> int:
    if not data:
        return 0
    padding = data.count("=")
    return max(0, (len(data) * 3) // 4 - padding)


def _safe_message_payload(message: dict[str, Any]) -> dict[str, Any]:
    payload = dict(message)
    if "data" in payload and isinstance(payload["data"], str):
        payload["data"] = {
            "kind": "string",
            "length": len(payload["data"]),
        }
    return payload


def _summarize_page_state(page_state: dict[str, Any]) -> dict[str, Any]:
    page_state = page_state or {}
    return {
        "url": page_state.get("url", ""),
        "title": page_state.get("title", ""),
        "viewport": page_state.get("viewport") or {},
        "scroll": page_state.get("scroll") or {},
        "focused_element": (page_state.get("focusedElement") or {}).get("text", ""),
        "interactive_count": len(page_state.get("interactives") or []),
        "form_count": len(page_state.get("forms") or []),
        "heading_count": len(page_state.get("headings") or []),
        "landmark_count": len(page_state.get("landmarks") or []),
    }


def _summarize_screenshot(screenshot: dict[str, Any]) -> dict[str, Any]:
    screenshot = screenshot or {}
    return {
        "mime_type": screenshot.get("mimeType", "image/png"),
        "size_bytes": _estimate_base64_size(str(screenshot.get("data") or "")),
    }


def _summarize_action_result(result: dict[str, Any]) -> dict[str, Any]:
    result = result or {}
    return {
        "success": result.get("success", False),
        "action": result.get("action", ""),
        "error": result.get("error", ""),
        "data": result.get("data") or {},
        "page_state": _summarize_page_state(result.get("page_state") or {}),
        "screenshot": _summarize_screenshot(result.get("screenshot") or {}),
    }


def _summarize_function_response(
    response: types.FunctionResponse | dict[str, Any],
) -> dict[str, Any]:
    if isinstance(response, dict):
        return response

    return {
        "id": getattr(response, "id", None),
        "name": getattr(response, "name", None),
        "response": getattr(response, "response", None),
        "will_continue": getattr(response, "will_continue", None),
        "scheduling": str(getattr(response, "scheduling", None)),
    }


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
