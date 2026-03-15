"""Tests for the page map builder."""

from app.page_map import build_page_map_prompt, extract_actionable_targets


def test_build_page_map_basic():
    state = {
        "url": "https://example.com",
        "title": "Test Page",
        "landmarks": [{"role": "navigation", "label": "Main nav"}],
        "headings": [{"level": 1, "text": "Welcome"}],
        "forms": [],
        "interactives": [],
        "images": [],
        "liveRegions": [],
        "accessibilityIssues": [],
    }
    result = build_page_map_prompt(state)
    assert "Test Page" in result
    assert "Main nav" in result
    assert "H1: Welcome" in result


def test_build_page_map_with_issues():
    state = {
        "url": "https://example.com",
        "title": "Broken Page",
        "landmarks": [],
        "headings": [],
        "forms": [],
        "interactives": [
            {
                "tag": "div",
                "role": "button",
                "text": "Click me",
                "ariaLabel": "",
                "selector": "div.fake-btn",
                "issues": ["non-semantic-interactive"],
            }
        ],
        "images": [{"alt": "", "src": "img.png"}],
        "accessibilityIssues": [
            {"severity": "error", "description": "Missing lang", "element": "html"}
        ],
    }
    result = build_page_map_prompt(state)
    assert "non-semantic-interactive" in result
    assert "missing alt" in result.lower()
    assert "Missing lang" in result


def test_extract_actionable_targets():
    state = {
        "interactives": [
            {"selector": "#btn1", "text": "Submit", "tag": "button", "role": "button", "issues": []},
        ],
        "forms": [
            {
                "name": "Login",
                "fields": [
                    {"selector": "#email", "label": "Email", "type": "email"},
                ],
            }
        ],
    }
    targets = extract_actionable_targets(state)
    assert len(targets) == 2
    assert targets[0]["selector"] == "#btn1"
    assert targets[1]["selector"] == "#email"


def test_build_page_map_with_forms():
    state = {
        "url": "https://example.com/form",
        "title": "Form Page",
        "landmarks": [],
        "headings": [],
        "forms": [
            {
                "name": "Contact",
                "fields": [
                    {"type": "text", "label": "Name", "value": "", "required": True, "selector": "#name"},
                    {"type": "email", "label": "", "value": "", "required": False, "selector": "#email"},
                ],
            }
        ],
        "interactives": [],
        "images": [],
        "liveRegions": [],
        "accessibilityIssues": [],
    }
    result = build_page_map_prompt(state)
    assert "Contact" in result
    assert "Name" in result
    assert "required" in result
