"""Tests for the action planner."""

from app.planner import plan_actions, ActionTier


def test_exact_match_semantic():
    action = {"name": "click", "args": {"target": "Submit"}}
    page_state = {
        "interactives": [
            {"selector": "#submit-btn", "text": "Submit", "ariaLabel": "", "tag": "button"},
        ],
        "forms": [],
    }
    result = plan_actions(action, page_state)
    assert result["tier"] == ActionTier.SEMANTIC
    assert result["execution"]["method"] == "content_script"


def test_multiple_matches_visual():
    action = {"name": "click", "args": {"target": "Submit"}}
    page_state = {
        "interactives": [
            {"selector": "#btn1", "text": "Submit Order", "ariaLabel": "", "tag": "button"},
            {"selector": "#btn2", "text": "Submit Form", "ariaLabel": "", "tag": "button"},
        ],
        "forms": [],
    }
    result = plan_actions(action, page_state)
    assert result["tier"] == ActionTier.SEMANTIC_VISUAL


def test_no_match_visual_fallback():
    action = {"name": "click", "args": {"target": "hidden widget"}}
    page_state = {
        "interactives": [
            {"selector": "#btn1", "text": "Something else", "ariaLabel": "", "tag": "button"},
        ],
        "forms": [],
    }
    result = plan_actions(action, page_state)
    assert result["tier"] == ActionTier.VISUAL_FALLBACK


def test_css_selector_exact():
    action = {"name": "click", "args": {"target": "#submit-btn"}}
    page_state = {
        "interactives": [
            {"selector": "#submit-btn", "text": "Go", "ariaLabel": "", "tag": "button"},
        ],
        "forms": [],
    }
    result = plan_actions(action, page_state)
    assert result["tier"] == ActionTier.SEMANTIC
    assert result["execution"]["selector"] == "#submit-btn"


def test_form_field_match():
    action = {"name": "click", "args": {"target": "Email"}}
    page_state = {
        "interactives": [],
        "forms": [
            {
                "name": "Login",
                "fields": [
                    {"selector": "#email", "label": "Email", "type": "email"},
                ],
            }
        ],
    }
    result = plan_actions(action, page_state)
    assert result["tier"] == ActionTier.SEMANTIC
