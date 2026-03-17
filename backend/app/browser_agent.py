"""Unified browser agent with DOM tools and optional Computer Use fallback."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable

from google import genai
from google.genai import errors as genai_errors, types

from app.page_map import build_page_map_prompt, extract_actionable_targets

logger = logging.getLogger("northstar.browser")

BROWSER_AGENT_MODEL = os.getenv("BROWSER_AGENT_MODEL", "gemini-2.5-flash")
ORCHESTRATOR_MODEL = BROWSER_AGENT_MODEL
COMPUTER_USE_MODEL = BROWSER_AGENT_MODEL
_COMPUTER_USE_OVERRIDE = os.getenv("BROWSER_AGENT_ENABLE_COMPUTER_USE", "auto").strip().lower()
MAX_ORCHESTRATOR_STEPS = int(os.getenv("BROWSER_ORCHESTRATOR_MAX_STEPS", "20"))
ORCHESTRATOR_RESPONSE_TIMEOUT_SECONDS = float(
    os.getenv("BROWSER_ORCHESTRATOR_TIMEOUT_SECONDS", "45")
)
ORCHESTRATOR_HEARTBEAT_SECONDS = float(
    os.getenv("BROWSER_ORCHESTRATOR_HEARTBEAT_SECONDS", "12")
)
FREE_TIER_DAILY_QUOTA_ID = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
BLOCKER_MARKERS = (
    "could not",
    "couldn't",
    "cannot",
    "can't",
    "unable",
    "not able",
    "failed to",
    "there was an issue",
    "there is an issue",
    "ran into an issue",
    "there was a problem",
    "there is a problem",
    "ran into a problem",
    "blocked",
    "not complete",
    "not completed",
)
READ_ONLY_GOAL_MARKERS = (
    "what is on",
    "what's on",
    "tell me what",
    "describe",
    "read",
    "summarize",
    "summary",
    "find the price",
    "what is the price",
    "what's the price",
    "how much",
    "price",
    "cost",
    "compare",
    "difference",
)
READ_ONLY_ACTION_BLOCK_MARKERS = (
    "buy",
    "bag",
    "cart",
    "checkout",
    "purchase",
    "order",
    "add to",
)
NAVIGATION_GOAL_MARKERS = (
    "open ",
    "go to",
    "take me to",
    "navigate to",
    "bring me to",
    "show me",
)
NAVIGATION_GOAL_STOPWORDS = {
    "a",
    "an",
    "go",
    "goto",
    "me",
    "navigate",
    "open",
    "page",
    "please",
    "show",
    "site",
    "take",
    "the",
    "to",
    "website",
}

# The pinned SDK exposes thinking_budget, not thinking_level. Use automatic
# model-managed thinking for the Gemini 3 browser loop.
AUTOMATIC_THINKING_CONFIG = types.ThinkingConfig(thinking_budget=-1)

BROWSER_AGENT_SYSTEM_INSTRUCTION = """You are Northstar's browser agent.

You are the coordinator and tool user behind a live voice assistant for browser control.

Rules:
- Keep the user's original goal active until it is completed, blocked, or clarified.
- Prefer the direct DOM tools first when the page map gives you a clear target.
- When the page map shows selector="...", pass only that selector to DOM tools. Do not pass the whole descriptive line.
- Use Computer Use only as a fallback when the DOM tools are ambiguous, the target is visually apparent but not semantically clear, or a direct DOM action failed.
- For navigation goals like opening a product page or moving to a section, prefer a direct DOM selector click or navigate to an exact URL.
- Do not use visual coordinate clicks for navigation when the page map already contains a matching selector.
- For informational goals like describing a page, finding a price, or comparing products, prefer get_page_map and read_element before clicking or navigating.
- Do not click purchase CTAs or navigate into store or checkout flows for read-only requests unless the user explicitly asked to buy or navigate there.
- If you are already on the relevant page, extract the answer from the current page instead of scrolling repeatedly.
- Stay on the current browser tab. Do not open a new browser window.
- Use at most one browser action per response.
- Use report_progress sparingly for short spoken updates.
- Do not call report_progress twice in a row.
- Use ask_user only when you genuinely need clarification or approval beyond existing safety checks.
- Use finish_task only when the user's goal is complete or you have a concise final summary.
- Do not claim an action succeeded unless the tool response confirms it.
- Keep messages concise and spoken-friendly because Gemini Live will say them aloud.
"""

BROWSER_AGENT_TOOL_DECLARATIONS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="report_progress",
                description="Send a short spoken progress update back to the user while work continues.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "message": types.Schema(
                            type="STRING",
                            description="A short, user-facing update in plain language.",
                        )
                    },
                    required=["message"],
                ),
            ),
            types.FunctionDeclaration(
                name="click",
                description="Click a target using a CSS selector. If the page map includes selector=\"...\", pass only that selector.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "target": types.Schema(
                            type="STRING",
                            description="CSS selector only.",
                        )
                    },
                    required=["target"],
                ),
            ),
            types.FunctionDeclaration(
                name="type_text",
                description="Type text into a target using a CSS selector. If the page map includes selector=\"...\", pass only that selector.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "target": types.Schema(
                            type="STRING",
                            description="CSS selector only.",
                        ),
                        "text": types.Schema(
                            type="STRING",
                            description="Text to enter.",
                        ),
                    },
                    required=["target", "text"],
                ),
            ),
            types.FunctionDeclaration(
                name="scroll",
                description="Scroll the current page by a named amount or pixel count.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "direction": types.Schema(
                            type="STRING",
                            enum=["up", "down", "left", "right"],
                            description="Direction to scroll.",
                        ),
                        "amount": types.Schema(
                            type="STRING",
                            description="Scroll amount such as small, medium, large, or a pixel count.",
                        ),
                    },
                    required=["direction"],
                ),
            ),
            types.FunctionDeclaration(
                name="navigate",
                description="Navigate the current tab to a specific URL.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "url": types.Schema(
                            type="STRING",
                            description="Absolute URL to open in the current tab.",
                        )
                    },
                    required=["url"],
                ),
            ),
            types.FunctionDeclaration(
                name="read_element",
                description="Read the text content of an element identified by CSS selector. If the page map includes selector=\"...\", pass only that selector.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "target": types.Schema(
                            type="STRING",
                            description="CSS selector only.",
                        )
                    },
                    required=["target"],
                ),
            ),
            types.FunctionDeclaration(
                name="get_page_map",
                description="Refresh the structured page context.",
                parameters=types.Schema(type="OBJECT"),
            ),
            types.FunctionDeclaration(
                name="highlight",
                description="Temporarily highlight an element identified by CSS selector. If the page map includes selector=\"...\", pass only that selector.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "target": types.Schema(
                            type="STRING",
                            description="CSS selector only.",
                        )
                    },
                    required=["target"],
                ),
            ),
            types.FunctionDeclaration(
                name="ask_user",
                description="Ask the user a concise clarifying question when the task cannot safely continue.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "question": types.Schema(
                            type="STRING",
                            description="The short question Northstar should ask the user.",
                        )
                    },
                    required=["question"],
                ),
            ),
            types.FunctionDeclaration(
                name="finish_task",
                description="Return the final outcome once the browser goal is complete or definitively blocked.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "summary": types.Schema(
                            type="STRING",
                            description="Short final summary for the user.",
                        )
                    },
                    required=["summary"],
                ),
            ),
        ]
    )
]

COMPUTER_USE_EXCLUDED_FUNCTIONS = [
    "open_web_browser",
    "navigate",
    "search",
    "go_back",
    "go_forward",
    "hover_at",
    "scroll_at",
    "drag_and_drop",
]

COMPUTER_USE_TOOL = types.Tool(
    computer_use=types.ComputerUse(
        environment=types.Environment.ENVIRONMENT_BROWSER,
        excluded_predefined_functions=COMPUTER_USE_EXCLUDED_FUNCTIONS,
    )
)

ActionExecutor = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
StatusCallback = Callable[[dict[str, Any]], Awaitable[None]]
ProgressCallback = Callable[[str], Awaitable[None]]


class BrowserAgent:
    """Runs a unified Gemini 3 Flash loop for browser actions."""

    def __init__(self, client: genai.Client):
        self.client = client

    async def run_task(
        self,
        goal: str,
        session: dict[str, Any],
        execute_action: ActionExecutor,
        send_status: StatusCallback | None = None,
        send_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        try:
            await self._record_event(
                session,
                event_type="task_started",
                payload={"goal": goal},
            )
            if not goal.strip():
                result = self._build_task_result(
                    goal=goal,
                    session=session,
                    status="failed",
                    summary="No browser goal was provided.",
                    mode="orchestrator",
                )
                await self._record_event(
                    session,
                    event_type="task_finished",
                    payload=result,
                )
                return result

            page_state = session.get("page_state") or {}
            if not page_state and not session.get("last_screenshot"):
                result = self._build_task_result(
                    goal=goal,
                    session=session,
                    status="retry",
                    summary="I do not have the current page context yet. Please try again in a moment.",
                    mode="orchestrator",
                    recoverable=True,
                    retry_goal=goal,
                )
                await self._record_event(
                    session,
                    event_type="missing_context",
                    payload=result,
                )
                return result

            result = await self._run_agent_loop(
                goal=goal,
                session=session,
                execute_action=execute_action,
                send_status=send_status,
                send_progress=send_progress,
            )
            await self._record_event(
                session,
                event_type="task_finished",
                payload=result,
            )
            return result
        except genai_errors.APIError as exc:
            logger.warning(
                "Gemini API error while running browser task: code=%s status=%s details=%s",
                getattr(exc, "code", None),
                getattr(exc, "status", None),
                getattr(exc, "details", None),
            )
            result = self._build_api_error_result(goal=goal, session=session, error=exc)
            await self._record_event(
                session,
                event_type="api_error",
                payload={
                    "goal": goal,
                    "code": getattr(exc, "code", None),
                    "status": getattr(exc, "status", None),
                    "details": getattr(exc, "details", None),
                    "result": result,
                },
            )
            return result

    async def _run_agent_loop(
        self,
        *,
        goal: str,
        session: dict[str, Any],
        execute_action: ActionExecutor,
        send_status: StatusCallback | None,
        send_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        contents = [self._build_agent_input(goal=goal, session=session)]
        consecutive_scroll_actions = 0

        for step in range(MAX_ORCHESTRATOR_STEPS):
            logger.info(
                "Browser agent step %s/%s starting for goal=%r",
                step + 1,
                MAX_ORCHESTRATOR_STEPS,
                goal,
            )
            await self._record_event(
                session,
                event_type="step_started",
                payload={"goal": goal, "step": step + 1},
            )
            if send_status:
                await send_status(
                    {
                        "phase": "browser_agent",
                        "step": step + 1,
                        "message": "Planning the next browser step.",
                    }
                )

            if step == 0 and send_progress:
                await send_progress("I'm reviewing the page and planning the first step.")

            try:
                response = await self._generate_agent_response_with_updates(
                    contents=contents,
                    goal=goal,
                    send_progress=send_progress,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Browser agent step %s timed out after %ss for goal=%r",
                    step + 1,
                    ORCHESTRATOR_RESPONSE_TIMEOUT_SECONDS,
                    goal,
                )
                result = self._build_task_result(
                    goal=goal,
                    session=session,
                    status="retry",
                    summary=(
                        "Northstar took too long planning the next browser step. "
                        "Please try again."
                    ),
                    mode="orchestrator",
                    recoverable=True,
                    retry_goal=goal,
                )
                await self._record_event(
                    session,
                    event_type="step_timeout",
                    payload={"goal": goal, "step": step + 1, "result": result},
                )
                return result

            assistant_text, function_calls = _extract_response(response)
            logger.info(
                "Browser agent step %s returned tools=%s text=%r",
                step + 1,
                [call.name for call in function_calls],
                assistant_text[:240] if assistant_text else "",
            )
            await self._record_event(
                session,
                event_type="model_response",
                payload={
                    "goal": goal,
                    "step": step + 1,
                    "assistant_text": assistant_text,
                    "function_calls": [
                        {
                            "name": call.name,
                            "args": dict(call.args or {}),
                        }
                        for call in function_calls
                    ],
                },
            )

            if not function_calls:
                summary = assistant_text or "The browser task is complete."
                status = "failed" if _looks_like_blocker_summary(summary) else "completed"
                return self._build_task_result(
                    goal=goal,
                    session=session,
                    status=status,
                    summary=summary,
                    mode="orchestrator",
                )

            response_parts: list[types.Part] = []

            for function_call in function_calls:
                name = function_call.name
                args = dict(function_call.args or {})

                if name == "report_progress":
                    message = str(args.get("message", "")).strip()
                    await self._record_event(
                        session,
                        event_type="progress_requested",
                        payload={"step": step + 1, "message": message},
                    )
                    if message and send_progress:
                        await send_progress(message)
                    response_parts.append(
                        self._build_function_response_part(
                            name=name,
                            response={"ok": True, "message": message},
                        )
                    )
                    continue

                if name == "ask_user":
                    question = str(args.get("question", "")).strip() or (
                        assistant_text or "What would you like me to do next?"
                    )
                    await self._record_event(
                        session,
                        event_type="needs_user_input",
                        payload={"step": step + 1, "question": question},
                    )
                    return self._build_task_result(
                        goal=goal,
                        session=session,
                        status="needs_input",
                        summary=question,
                        mode="orchestrator",
                        user_question=question,
                    )

                if name == "finish_task":
                    summary = str(args.get("summary", "")).strip() or (
                        assistant_text or "The browser task is complete."
                    )
                    await self._record_event(
                        session,
                        event_type="finish_requested",
                        payload={"step": step + 1, "summary": summary},
                    )
                    return self._build_task_result(
                        goal=goal,
                        session=session,
                        status="completed",
                        summary=summary,
                        mode="orchestrator",
                    )

                action = self._map_tool_call(function_call, session=session)
                if action is None:
                    await self._record_event(
                        session,
                        event_type="unsupported_action",
                        payload={"step": step + 1, "name": name, "args": args},
                    )
                    return self._build_task_result(
                        goal=goal,
                        session=session,
                        status="failed",
                        summary=f"Unsupported browser-agent action: {name}",
                        mode="orchestrator",
                    )

                rewritten_action, rewrite_reason = self._coerce_action_for_goal(
                    goal=goal,
                    session=session,
                    action=action,
                )
                if rewrite_reason:
                    await self._record_event(
                        session,
                        event_type="action_rewritten",
                        payload={
                            "step": step + 1,
                            "tool_name": name,
                            "tool_args": args,
                            "original_action": action,
                            "rewritten_action": rewritten_action,
                            "reason": rewrite_reason,
                        },
                    )
                    action = rewritten_action

                if send_status:
                    await send_status(
                        {
                            "phase": "browser_agent",
                            "step": step + 1,
                            "message": self._describe_action(function_call, action),
                        }
                    )

                guard_error = self._guard_action_for_goal(
                    goal=goal,
                    session=session,
                    action=action,
                    consecutive_scroll_actions=consecutive_scroll_actions,
                )
                if guard_error:
                    await self._record_event(
                        session,
                        event_type="action_blocked",
                        payload={
                            "step": step + 1,
                            "tool_name": name,
                            "tool_args": args,
                            "action": action,
                            "reason": guard_error,
                        },
                    )
                    response_parts.append(
                        self._build_function_response_part(
                            name=name,
                            response={
                                "success": False,
                                "error": guard_error,
                                "page_context": self._build_page_context(session),
                            },
                        )
                    )
                    consecutive_scroll_actions = 0
                    continue

                await self._record_event(
                    session,
                    event_type="action_planned",
                    payload={
                        "step": step + 1,
                        "tool_name": name,
                        "tool_args": args,
                        "action": action,
                    },
                )
                result = await execute_action(action)
                if action.get("name") == "scroll":
                    consecutive_scroll_actions += 1
                else:
                    consecutive_scroll_actions = 0
                await self._record_event(
                    session,
                    event_type="action_result",
                    payload={
                        "step": step + 1,
                        "tool_name": name,
                        "success": result.get("success", False),
                        "action": result.get("action", ""),
                        "error": result.get("error", ""),
                        "data": result.get("data") or {},
                    },
                )
                response_parts.append(
                    self._build_function_response_part(
                        name=name,
                        response=self._build_action_response_payload(result),
                        screenshot=result.get("screenshot"),
                    )
                )

            contents.append(response.candidates[0].content)
            contents.append(types.Content(role="user", parts=response_parts))

        question = (
            "I need a bit more time to keep working on this. "
            "Say continue or tap Continue and I'll resume from the current page."
        )
        result = self._build_task_result(
            goal=goal,
            session=session,
            status="needs_input",
            summary=question,
            mode="orchestrator",
            recoverable=True,
            retry_goal=goal,
            user_question=question,
            continuation_available=True,
        )
        await self._record_event(
            session,
            event_type="step_limit_reached",
            payload=result,
        )
        return result

    async def _generate_agent_response_with_updates(
        self,
        *,
        contents: list[types.Content],
        goal: str,
        send_progress: ProgressCallback | None,
    ):
        response_task = asyncio.create_task(
            self._generate_agent_response(contents=contents)
        )
        started_at = time.monotonic()
        heartbeat_messages = [
            "I'm still working through the page.",
            "Still here. I'm deciding the next browser action.",
        ]
        heartbeat_index = 0

        try:
            while True:
                elapsed = time.monotonic() - started_at
                remaining = ORCHESTRATOR_RESPONSE_TIMEOUT_SECONDS - elapsed
                if remaining <= 0:
                    response_task.cancel()
                    raise asyncio.TimeoutError()

                wait_for_seconds = min(ORCHESTRATOR_HEARTBEAT_SECONDS, remaining)
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(response_task),
                        timeout=wait_for_seconds,
                    )
                except asyncio.TimeoutError:
                    if send_progress and heartbeat_index < len(heartbeat_messages):
                        logger.info(
                            "Browser agent still planning after %.1fs for goal=%r",
                            time.monotonic() - started_at,
                            goal,
                        )
                        await send_progress(heartbeat_messages[heartbeat_index])
                        heartbeat_index += 1
        finally:
            if not response_task.done():
                response_task.cancel()

    async def _generate_agent_response(self, *, contents: list[types.Content]):
        tools = list(BROWSER_AGENT_TOOL_DECLARATIONS)
        system_instruction = BROWSER_AGENT_SYSTEM_INSTRUCTION
        if _computer_use_enabled_for_model(BROWSER_AGENT_MODEL):
            tools.append(COMPUTER_USE_TOOL)
        else:
            system_instruction = (
                f"{BROWSER_AGENT_SYSTEM_INSTRUCTION}\n"
                "- Computer Use is unavailable in this runtime. Stay within the direct DOM tools only.\n"
            )

        return await asyncio.to_thread(
            self.client.models.generate_content,
            model=BROWSER_AGENT_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.0,
                system_instruction=system_instruction,
                tools=tools,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
                thinking_config=AUTOMATIC_THINKING_CONFIG,
            ),
        )

    def _build_agent_input(
        self,
        *,
        goal: str,
        session: dict[str, Any],
    ) -> types.Content:
        parts: list[types.Part] = [
            types.Part(
                text=(
                    f"User goal: {goal}\n\n"
                    f"Task mode: {'read_only' if _is_read_only_goal(goal) else 'interactive'}\n\n"
                    "Prefer direct DOM tools when the page map clearly identifies the target. "
                    "Use Computer Use only if you need visual fallback. "
                    'For DOM tools, pass only selector="..." values from the page map.\n\n'
                    f"[Current page state]\n{self._build_page_context(session)}"
                )
            )
        ]

        screenshot = session.get("last_screenshot")
        if screenshot:
            try:
                parts.append(
                    types.Part(
                        inline_data=types.Blob(
                            mime_type="image/png",
                            data=base64.b64decode(screenshot),
                        )
                    )
                )
            except Exception:
                logger.warning("Failed to decode screenshot for browser agent prompt.")

        return types.Content(role="user", parts=parts)

    def _build_page_context(self, session: dict[str, Any]) -> str:
        page_state = session.get("page_state", {}) or {}
        return build_page_map_prompt(page_state) if page_state else "No page map available."

    def _build_action_response_payload(self, result: dict[str, Any]) -> dict[str, Any]:
        page_state = result.get("page_state") or {}
        return {
            "success": result.get("success", False),
            "action": result.get("action", ""),
            "url": page_state.get("url", ""),
            "title": page_state.get("title", ""),
            "focused_element": (
                ((page_state.get("focusedElement") or {}).get("text", ""))
            ),
            "error": result.get("error", ""),
            "data": result.get("data") or {},
            "page_context": build_page_map_prompt(page_state)
            if page_state
            else "No page map available.",
        }

    def _build_function_response_part(
        self,
        *,
        name: str,
        response: dict[str, Any],
        screenshot: dict[str, Any] | None = None,
    ) -> types.Part:
        return types.Part(
            function_response=types.FunctionResponse(
                name=name,
                response=response,
            )
        )

    def _describe_action(
        self,
        function_call: types.FunctionCall,
        action: dict[str, Any],
    ) -> str:
        if action.get("mode") == "computer_use":
            return f"Using visual browser controls: {function_call.name}."

        name = action.get("name")
        args = dict(action.get("args") or {})
        if name == "click":
            return f'Clicking {args.get("target", "the target")}.'
        if name == "type_text":
            return f'Typing into {args.get("target", "the field")}.'
        if name == "scroll":
            return f'Scrolling {args.get("direction", "the page")}.'
        if name == "navigate":
            return "Opening the requested page."
        if name == "read_element":
            return f'Reading {args.get("target", "the element")}.'
        if name == "get_page_map":
            return "Refreshing the page context."
        if name == "highlight":
            return f'Highlighting {args.get("target", "the target")}.'
        return "Working on the page."

    def _map_tool_call(
        self,
        function_call: types.FunctionCall,
        *,
        session: dict[str, Any],
    ) -> dict[str, Any] | None:
        args = dict(function_call.args or {})
        name = function_call.name

        if name == "click":
            return {
                "mode": "dom",
                "name": "click",
                "args": {
                    "target": self._canonicalize_dom_target(
                        args.get("target", ""),
                        session=session,
                    )
                },
            }
        if name == "type_text":
            return {
                "mode": "dom",
                "name": "type_text",
                "args": {
                    "target": self._canonicalize_dom_target(
                        args.get("target", ""),
                        session=session,
                    ),
                    "text": args.get("text", ""),
                },
            }
        if name == "scroll":
            return {
                "mode": "dom",
                "name": "scroll",
                "args": {
                    "direction": args.get("direction", "down"),
                    "amount": str(args.get("amount", "medium")),
                },
            }
        if name == "navigate":
            return {"mode": "dom", "name": "navigate", "args": {"url": args.get("url", "")}}
        if name == "read_element":
            return {
                "mode": "dom",
                "name": "read_element",
                "args": {
                    "target": self._canonicalize_dom_target(
                        args.get("target", ""),
                        session=session,
                    )
                },
            }
        if name == "get_page_map":
            return {"mode": "dom", "name": "get_page_map", "args": {}}
        if name == "highlight":
            return {
                "mode": "dom",
                "name": "highlight",
                "args": {
                    "target": self._canonicalize_dom_target(
                        args.get("target", ""),
                        session=session,
                    )
                },
            }
        return self._map_computer_use_call(function_call)

    def _map_computer_use_call(
        self,
        function_call: types.FunctionCall,
    ) -> dict[str, Any] | None:
        args = dict(function_call.args or {})
        name = function_call.name

        if name == "click_at":
            return {"mode": "computer_use", "name": "click_at", "args": args}
        if name == "type_text_at":
            return {
                "mode": "computer_use",
                "name": "type_text",
                "args": {
                    "x": args.get("x"),
                    "y": args.get("y"),
                    "text": args.get("text", ""),
                    "press_enter": args.get("press_enter", False),
                },
            }
        if name == "key_combination":
            return {
                "mode": "computer_use",
                "name": "keypress",
                "args": {"key": args.get("keys") or args.get("key") or ""},
            }
        if name == "scroll_document":
            direction = (args.get("direction") or "down").lower()
            amount = 720 if direction == "down" else -720
            return {
                "mode": "computer_use",
                "name": "scroll_by",
                "args": {"dx": 0, "dy": amount},
            }
        if name == "wait_5_seconds":
            return {
                "mode": "computer_use",
                "name": "wait",
                "args": {"ms": 5000},
            }
        return None

    def _guard_action_for_goal(
        self,
        *,
        goal: str,
        session: dict[str, Any],
        action: dict[str, Any],
        consecutive_scroll_actions: int,
    ) -> str | None:
        navigation_selector = self._resolve_navigation_selector(goal, session)

        if (
            navigation_selector
            and _is_navigation_goal(goal)
            and action.get("name") == "scroll"
        ):
            return (
                "This is a navigation task and the page map already contains a likely target. "
                "Use the matching DOM selector or navigate directly instead of scrolling."
            )

        if action.get("name") == "scroll" and consecutive_scroll_actions >= 2:
            return (
                "You have already scrolled multiple times without finding the answer. "
                "Use get_page_map, read_element, or finish_task from the current page instead."
            )

        if (
            navigation_selector
            and _is_navigation_goal(goal)
            and action.get("mode") == "computer_use"
            and action.get("name") == "click_at"
        ):
            return (
                "This is a page-navigation task and the page map already has a matching selector. "
                "Use the DOM click tool with that selector instead of a coordinate click."
            )

        if not _is_read_only_goal(goal):
            return None

        name = str(action.get("name") or "")
        args = dict(action.get("args") or {})
        if name == "type_text":
            return (
                "This is a read-only request. Do not type into the page unless the user asked to edit or submit something."
            )

        if name == "click":
            target = str(args.get("target") or "").casefold()
            if any(marker in target for marker in READ_ONLY_ACTION_BLOCK_MARKERS):
                return (
                    "This is a read-only request. Do not click purchase or checkout controls. "
                    "Read the current page instead."
                )

        if name == "navigate":
            url = str(args.get("url") or "").casefold()
            if any(marker in url for marker in ("/shop/", "/buy", "/checkout", "/bag")):
                return (
                    "This is a read-only request. Do not navigate into store or checkout flows unless the user asked for that."
                )

        return None

    def _coerce_action_for_goal(
        self,
        *,
        goal: str,
        session: dict[str, Any],
        action: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        selector = self._resolve_navigation_selector(goal, session)
        if not selector or not _is_navigation_goal(goal):
            return action, None

        name = str(action.get("name") or "")
        if name in {"scroll", "click_at"}:
            return (
                {
                    "mode": "dom",
                    "name": "click",
                    "args": {"target": selector},
                },
                "Rewrote the action to a DOM selector click because this is a navigation task with a unique matching page-map target.",
            )

        return action, None

    def _canonicalize_dom_target(
        self,
        raw_target: Any,
        *,
        session: dict[str, Any],
    ) -> str:
        target = str(raw_target or "").strip()
        if not target:
            return ""

        inline_selector = _extract_selector_from_mixed_target(target)
        if inline_selector:
            return inline_selector

        actionable_targets = extract_actionable_targets(session.get("page_state") or {})
        exact_matches: list[str] = []
        partial_matches: list[str] = []
        target_lower = target.casefold()
        target_compact = _normalize_whitespace(target_lower)

        for candidate in actionable_targets:
            selector = str(candidate.get("selector") or "").strip()
            if not selector:
                continue
            description = str(candidate.get("description") or "").strip()
            role = str(candidate.get("role") or "").strip()
            tag = str(candidate.get("tag") or "").strip()
            haystacks = {
                selector.casefold(),
                description.casefold(),
                _normalize_whitespace(description.casefold()),
                f'<{tag}> role={role} "{description}"'.casefold(),
                f'selector="{selector}"'.casefold(),
            }
            if target_lower in haystacks or target_compact in haystacks:
                exact_matches.append(selector)
                continue
            if any(target_lower and target_lower in haystack for haystack in haystacks):
                partial_matches.append(selector)

        if len(set(exact_matches)) == 1:
            return exact_matches[0]
        if len(set(partial_matches)) == 1:
            return partial_matches[0]
        return target

    def _resolve_navigation_selector(
        self,
        goal: str,
        session: dict[str, Any],
    ) -> str | None:
        if not _is_navigation_goal(goal):
            return None

        phrase, tokens = _extract_navigation_target_terms(goal)
        if not tokens:
            return None

        candidates: list[tuple[int, str]] = []
        for candidate in extract_actionable_targets(session.get("page_state") or {}):
            selector = str(candidate.get("selector") or "").strip()
            if not selector:
                continue
            description = str(candidate.get("description") or "").strip()
            haystack = f"{description} {selector}".casefold()
            score = 0
            if phrase and phrase in haystack:
                score += 10
            token_hits = sum(1 for token in tokens if token in haystack)
            if token_hits == len(tokens):
                score += 4 + token_hits
            elif token_hits:
                score += token_hits
            if score > 0:
                candidates.append((score, selector))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        top_score = candidates[0][0]
        top_selectors = sorted({selector for score, selector in candidates if score == top_score})
        if len(top_selectors) == 1 and top_score >= max(4, len(tokens)):
            return top_selectors[0]
        return None

    def _build_task_result(
        self,
        *,
        goal: str,
        session: dict[str, Any],
        status: str,
        summary: str,
        mode: str,
        recoverable: bool = False,
        retry_goal: str | None = None,
        user_question: str | None = None,
        continuation_available: bool = False,
    ) -> dict[str, Any]:
        page_state = session.get("page_state") or {}
        result = {
            "success": status == "completed",
            "status": status,
            "summary": summary,
            "mode": mode,
            "goal": goal,
            "recoverable": recoverable,
            "current_url": page_state.get("url", ""),
            "current_title": page_state.get("title", ""),
        }
        if retry_goal:
            result["retry_goal"] = retry_goal
        if user_question:
            result["user_question"] = user_question
        if continuation_available:
            result["continuation_available"] = True
        return result

    def _build_api_error_result(
        self,
        *,
        goal: str,
        session: dict[str, Any],
        error: genai_errors.APIError,
    ) -> dict[str, Any]:
        model_name = _extract_quota_model(error.details) or BROWSER_AGENT_MODEL
        retry_delay = _extract_retry_delay(error.details)

        if _is_free_tier_daily_quota_error(error):
            summary = (
                f"Northstar hit the daily free-tier Gemini limit for {model_name}. "
                "That limit usually resets at midnight Pacific. "
                "Please try again later or move this project off the free tier."
            )
            return self._build_task_result(
                goal=goal,
                session=session,
                status="failed",
                summary=summary,
                mode="orchestrator",
            )

        if getattr(error, "code", None) == 429:
            summary = f"Northstar hit a Gemini rate limit for {model_name}."
            if retry_delay:
                summary += f" Retry after about {retry_delay}."
            return self._build_task_result(
                goal=goal,
                session=session,
                status="failed",
                summary=summary,
                mode="orchestrator",
            )

        summary = getattr(error, "message", None) or str(error)
        return self._build_task_result(
            goal=goal,
            session=session,
            status="failed",
            summary=f"Northstar hit a Gemini API error while working on this page: {summary}",
            mode="orchestrator",
        )

    async def _record_event(
        self,
        session: dict[str, Any],
        *,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        recorder = session.get("recorder")
        if not recorder:
            return
        try:
            await recorder.log_event(
                source="browser_agent",
                event_type=event_type,
                payload=payload,
            )
        except Exception as exc:
            logger.warning("Browser agent session recording failed: %s", exc)


def _extract_response(
    response: Any,
) -> tuple[str, list[types.FunctionCall]]:
    if not getattr(response, "candidates", None):
        return "", []

    content = response.candidates[0].content
    if not content:
        return "", []

    text_parts: list[str] = []
    function_calls: list[types.FunctionCall] = []
    for part in content.parts:
        if getattr(part, "text", None):
            text_parts.append(part.text)
        if getattr(part, "function_call", None):
            function_calls.append(part.function_call)
    return " ".join(text_parts).strip(), function_calls


def _looks_like_blocker_summary(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    if any(lowered.startswith(marker) for marker in BLOCKER_MARKERS):
        return True

    return any(
        phrase in lowered
        for phrase in (
            "failed to ",
            "unable to ",
            "could not ",
            "couldn't ",
            "cannot ",
            "can't ",
            "not able to ",
            "blocked from ",
        )
    )


def _computer_use_enabled_for_model(model_name: str) -> bool:
    if _COMPUTER_USE_OVERRIDE in {"1", "true", "yes", "on"}:
        return True
    if _COMPUTER_USE_OVERRIDE in {"0", "false", "no", "off"}:
        return False

    normalized_model_name = str(model_name or "").strip().lower()
    return (
        "computer-use" in normalized_model_name
        or normalized_model_name == "gemini-3-flash-preview"
    )


def _is_read_only_goal(goal: str) -> bool:
    normalized_goal = " ".join(str(goal or "").casefold().split())
    if not normalized_goal:
        return False
    return any(marker in normalized_goal for marker in READ_ONLY_GOAL_MARKERS)


def _is_navigation_goal(goal: str) -> bool:
    normalized_goal = " ".join(str(goal or "").casefold().split())
    if not normalized_goal:
        return False
    return any(marker in normalized_goal for marker in NAVIGATION_GOAL_MARKERS)


def _extract_selector_from_mixed_target(target: str) -> str | None:
    normalized = str(target or "").strip()
    if not normalized:
        return None

    selector_match = re.search(r'selector="([^"]+)"', normalized)
    if selector_match:
        return selector_match.group(1).strip() or None

    if " | " in normalized:
        maybe_selector = normalized.rsplit(" | ", 1)[-1].strip()
        if _looks_like_selector(maybe_selector):
            return maybe_selector

    if _looks_like_selector(normalized):
        return normalized
    return None


def _looks_like_selector(target: str) -> bool:
    normalized = str(target or "").strip()
    if not normalized:
        return False
    return bool(re.search(r"[#.:\[\]>~=+]", normalized)) or " > " in normalized


def _normalize_whitespace(text: str) -> str:
    return " ".join(str(text or "").split())


def _extract_navigation_target_terms(goal: str) -> tuple[str, list[str]]:
    normalized = _normalize_whitespace(str(goal or "").casefold())
    normalized = re.sub(r"[^\w\s-]", " ", normalized)
    normalized = normalized.replace("-", " ")
    tokens = [
        token
        for token in normalized.split()
        if token and token not in NAVIGATION_GOAL_STOPWORDS
    ]
    phrase = " ".join(tokens)
    return phrase, tokens


def _is_free_tier_daily_quota_error(error: genai_errors.APIError) -> bool:
    if getattr(error, "code", None) != 429:
        return False
    for violation in _extract_quota_violations(error.details):
        if violation.get("quotaId") == FREE_TIER_DAILY_QUOTA_ID:
            return True
    return False


def _extract_quota_model(details: Any) -> str:
    for violation in _extract_quota_violations(details):
        model_name = (violation.get("quotaDimensions") or {}).get("model", "")
        if model_name:
            return model_name
    return ""


def _extract_quota_violations(details: Any) -> list[dict[str, Any]]:
    payload = details or {}
    error_payload = payload.get("error", payload) if isinstance(payload, dict) else {}
    detail_items = (
        error_payload.get("details", []) if isinstance(error_payload, dict) else []
    )
    violations: list[dict[str, Any]] = []
    for item in detail_items:
        if not isinstance(item, dict):
            continue
        if item.get("@type") != "type.googleapis.com/google.rpc.QuotaFailure":
            continue
        for violation in item.get("violations", []):
            if isinstance(violation, dict):
                violations.append(violation)
    return violations


def _extract_retry_delay(details: Any) -> str:
    payload = details or {}
    error_payload = payload.get("error", payload) if isinstance(payload, dict) else {}
    detail_items = (
        error_payload.get("details", []) if isinstance(error_payload, dict) else []
    )
    for item in detail_items:
        if not isinstance(item, dict):
            continue
        if item.get("@type") != "type.googleapis.com/google.rpc.RetryInfo":
            continue
        raw_delay = item.get("retryDelay", "")
        match = re.fullmatch(r"(\d+)s", str(raw_delay).strip())
        if match:
            seconds = int(match.group(1))
            return f"{seconds} seconds"
        if raw_delay:
            return str(raw_delay)
    return ""
