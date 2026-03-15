"""Tests for the Gemini 3 Flash browser agent."""

import asyncio
from types import SimpleNamespace

from google.genai import errors as genai_errors

from app.browser_agent import BrowserAgent, ORCHESTRATOR_MODEL


def make_responses(
    *,
    orchestrator_responses: list,
    worker_responses: list | None = None,
    shared_model_responses: list | None = None,
):
    del worker_responses
    return {
        ORCHESTRATOR_MODEL: list(shared_model_responses or orchestrator_responses)
    }


def make_function_response(name: str, args: dict):
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            text=None,
                            function_call=SimpleNamespace(name=name, args=args),
                        )
                    ]
                )
            )
        ]
    )


class FakeModels:
    def __init__(self, responses_by_model):
        self.responses_by_model = {
            model: list(responses) for model, responses in responses_by_model.items()
        }
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        queue = self.responses_by_model[model]
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses_by_model):
        self.models = FakeModels(responses_by_model)


def make_session():
    return {
        "page_state": {
            "url": "https://example.com/product",
            "title": "Product page",
            "viewport": {"width": 1000, "height": 1000},
            "interactives": [
                {
                    "selector": "#add-to-bag",
                    "text": "Add to Bag",
                    "ariaLabel": "Add to Bag",
                    "tag": "button",
                    "bounds": {"x": 420, "y": 300, "width": 180, "height": 80},
                }
            ],
            "forms": [],
        },
        "last_screenshot": None,
    }


def make_navigation_session():
    return {
        "page_state": {
            "url": "https://example.com/store",
            "title": "Store",
            "viewport": {"width": 1000, "height": 1000},
            "interactives": [
                {
                    "selector": "#macbook-pro-link",
                    "text": "MacBook Pro",
                    "ariaLabel": "MacBook Pro",
                    "tag": "a",
                    "bounds": {"x": 420, "y": 180, "width": 220, "height": 48},
                },
                {
                    "selector": "#macbook-air-link",
                    "text": "MacBook Air",
                    "ariaLabel": "MacBook Air",
                    "tag": "a",
                    "bounds": {"x": 420, "y": 240, "width": 220, "height": 48},
                },
            ],
            "forms": [],
        },
        "last_screenshot": None,
    }


def test_browser_agent_reports_progress_and_completes():
    client = FakeClient(
        make_responses(
            orchestrator_responses=[
                make_function_response(
                    "report_progress",
                    {"message": "I am finding the buy controls."},
                ),
                make_function_response(
                    "click",
                    {"target": "Add to Bag"},
                ),
                make_function_response(
                    "finish_task",
                    {"summary": "I added the MacBook Pro to the bag."},
                ),
            ],
        )
    )
    agent = BrowserAgent(client)
    actions = []
    progress_updates = []
    status_updates = []

    session = make_session()

    async def execute_action(action):
        actions.append(action)
        return {
            "action": action["name"],
            "success": True,
            "page_state": session["page_state"],
        }

    async def send_progress(message: str):
        progress_updates.append(message)

    async def send_status(data):
        status_updates.append(data)

    result = asyncio.run(
        agent.run_task(
            "add this MacBook Pro to the bag",
            session,
            execute_action,
            send_status=send_status,
            send_progress=send_progress,
        )
    )

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["mode"] == "orchestrator"
    assert result["summary"] == "I added the MacBook Pro to the bag."
    assert progress_updates == [
        "I'm reviewing the page and planning the first step.",
        "I am finding the buy controls.",
    ]
    assert actions == [
        {"mode": "dom", "name": "click", "args": {"target": "#add-to-bag"}}
    ]
    assert status_updates
    assert all(update["phase"] == "browser_agent" for update in status_updates)


def test_browser_agent_can_request_user_input():
    client = FakeClient(
        {
            ORCHESTRATOR_MODEL: [
                make_function_response(
                    "ask_user",
                    {"question": "Which storage size do you want?"},
                )
            ]
        }
    )
    agent = BrowserAgent(client)

    async def execute_action(action):
        raise AssertionError("No browser action should run when user input is needed")

    result = asyncio.run(
        agent.run_task("buy this laptop", make_session(), execute_action)
    )

    assert result["success"] is False
    assert result["status"] == "needs_input"
    assert result["mode"] == "orchestrator"
    assert result["summary"] == "Which storage size do you want?"
    assert result["user_question"] == "Which storage size do you want?"


def test_browser_agent_allows_computer_use_to_recover_after_failed_action():
    client = FakeClient(
        make_responses(
            orchestrator_responses=[
                make_function_response("click_at", {"x": 100, "y": 100}),
                make_function_response("click_at", {"x": 500, "y": 500}),
                make_function_response(
                    "finish_task",
                    {"summary": "The item is now in the bag."},
                ),
            ],
        )
    )
    agent = BrowserAgent(client)
    actions = []
    status_updates = []

    session = make_session()

    async def execute_action(action):
        actions.append(action)
        if len(actions) == 1:
            return {
                "action": action["name"],
                "success": False,
                "error": "Element not found at 100, 100",
                "page_state": session["page_state"],
            }

        return {
            "action": action["name"],
            "success": True,
            "page_state": session["page_state"],
        }

    async def send_status(data):
        status_updates.append(data)

    result = asyncio.run(
        agent.run_task(
            "add this to the bag",
            session,
            execute_action,
            send_status=send_status,
        )
    )

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["mode"] == "orchestrator"
    assert [action["args"] for action in actions] == [
        {"x": 100, "y": 100},
        {"x": 500, "y": 500},
    ]
    assert any(
        update["message"] == "Using visual browser controls: click_at."
        for update in status_updates
    )


def test_browser_agent_returns_retry_when_page_context_is_missing():
    client = FakeClient({ORCHESTRATOR_MODEL: []})
    agent = BrowserAgent(client)

    async def execute_action(action):
        raise AssertionError("No browser action should execute without page context")

    result = asyncio.run(
        agent.run_task(
            "describe this page",
            {"page_state": None, "last_screenshot": None},
            execute_action,
        )
    )

    assert result["success"] is False
    assert result["status"] == "retry"
    assert result["mode"] == "orchestrator"
    assert result["retry_goal"] == "describe this page"


def test_browser_agent_blocks_purchase_actions_for_read_only_goals():
    client = FakeClient(
        make_responses(
            orchestrator_responses=[
                make_function_response("click", {"target": "Buy"}),
                make_function_response(
                    "finish_task",
                    {"summary": "The product starts at $999."},
                ),
            ],
        )
    )
    agent = BrowserAgent(client)
    actions = []

    async def execute_action(action):
        actions.append(action)
        return {
            "action": action["name"],
            "success": True,
            "page_state": make_session()["page_state"],
        }

    result = asyncio.run(
        agent.run_task(
            "find the price of the product on the screen",
            make_session(),
            execute_action,
        )
    )

    assert result["success"] is True
    assert result["summary"] == "The product starts at $999."
    assert actions == []


def test_browser_agent_blocks_excessive_scroll_loops():
    client = FakeClient(
        make_responses(
            orchestrator_responses=[
                make_function_response("scroll", {"direction": "down"}),
                make_function_response("scroll", {"direction": "down"}),
                make_function_response("scroll", {"direction": "down"}),
                make_function_response(
                    "finish_task",
                    {"summary": "I found the comparison details already on the page."},
                ),
            ],
        )
    )
    agent = BrowserAgent(client)
    actions = []
    session = make_session()

    async def execute_action(action):
        actions.append(action)
        return {
            "action": action["name"],
            "success": True,
            "page_state": session["page_state"],
        }

    result = asyncio.run(
        agent.run_task(
            "compare the two products on this page",
            session,
            execute_action,
        )
    )

    assert result["success"] is True
    assert result["summary"] == "I found the comparison details already on the page."
    assert actions == [
        {"mode": "dom", "name": "scroll", "args": {"direction": "down", "amount": "medium"}},
        {"mode": "dom", "name": "scroll", "args": {"direction": "down", "amount": "medium"}},
    ]


def test_browser_agent_canonicalizes_mixed_page_map_targets():
    client = FakeClient(
        make_responses(
            orchestrator_responses=[
                make_function_response(
                    "click",
                    {"target": '<button> role=button "Add to Bag" | #add-to-bag'},
                ),
                make_function_response(
                    "finish_task",
                    {"summary": "Done."},
                ),
            ],
        )
    )
    agent = BrowserAgent(client)
    actions = []
    session = make_session()

    async def execute_action(action):
        actions.append(action)
        return {
            "action": action["name"],
            "success": True,
            "page_state": session["page_state"],
        }

    result = asyncio.run(
        agent.run_task(
            "add this MacBook Pro to the bag",
            session,
            execute_action,
        )
    )

    assert result["success"] is True
    assert actions == [
        {"mode": "dom", "name": "click", "args": {"target": "#add-to-bag"}}
    ]


def test_browser_agent_rewrites_visual_navigation_clicks_to_dom_clicks():
    client = FakeClient(
        make_responses(
            orchestrator_responses=[
                make_function_response("click_at", {"x": 460, "y": 180}),
                make_function_response(
                    "finish_task",
                    {"summary": "The MacBook Pro page is open."},
                ),
            ],
        )
    )
    agent = BrowserAgent(client)
    actions = []
    session = make_navigation_session()

    async def execute_action(action):
        actions.append(action)
        return {
            "action": action["name"],
            "success": True,
            "page_state": session["page_state"],
        }

    result = asyncio.run(
        agent.run_task(
            "open the MacBook Pro page",
            session,
            execute_action,
        )
    )

    assert result["success"] is True
    assert actions == [
        {"mode": "dom", "name": "click", "args": {"target": "#macbook-pro-link"}}
    ]


def test_browser_agent_returns_friendly_message_for_free_tier_quota():
    client = FakeClient(
        {
            ORCHESTRATOR_MODEL: [
                genai_errors.ClientError(
                    429,
                    {
                        "error": {
                            "code": 429,
                            "message": "Quota exceeded for metric.",
                            "status": "RESOURCE_EXHAUSTED",
                            "details": [
                                {
                                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                    "violations": [
                                        {
                                            "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                                            "quotaDimensions": {"model": ORCHESTRATOR_MODEL},
                                        }
                                    ],
                                },
                                {
                                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                                    "retryDelay": "10s",
                                },
                            ],
                        }
                    },
                )
            ]
        }
    )
    agent = BrowserAgent(client)

    async def execute_action(action):
        raise AssertionError("No browser action should execute after a quota failure")

    result = asyncio.run(agent.run_task("describe this page", make_session(), execute_action))

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["mode"] == "orchestrator"
    assert "daily free-tier Gemini limit" in result["summary"]
    assert "midnight Pacific" in result["summary"]
