"""Tests for the action verification engine."""

from app.verifier import verify_action_result


def test_click_navigation():
    old = {"url": "https://example.com/page1", "title": "Page 1"}
    new = {"url": "https://example.com/page2", "title": "Page 2"}
    result = verify_action_result("click", old, new, True, "")
    assert result["verified"] is True
    assert "Page 2" in result["narration"]


def test_click_dialog():
    old = {"url": "https://example.com", "landmarks": []}
    new = {"url": "https://example.com", "landmarks": [{"role": "dialog"}]}
    result = verify_action_result("click", old, new, True, "")
    assert result["verified"] is True
    assert "dialog" in result["narration"].lower()


def test_failed_action():
    old = {"url": "https://example.com"}
    new = {"url": "https://example.com"}
    result = verify_action_result("click", old, new, False, "Element not found")
    assert result["verified"] is False
    assert "failed" in result["narration"].lower()


def test_scroll_verification():
    old = {"url": "https://example.com", "scrollPosition": {"x": 0, "y": 0}}
    new = {"url": "https://example.com", "scrollPosition": {"x": 0, "y": 400}}
    result = verify_action_result("scroll", old, new, True, "")
    assert result["verified"] is True


def test_type_verification():
    old = {"url": "https://example.com", "forms": []}
    new = {
        "url": "https://example.com",
        "forms": [{"fields": [{"label": "Email", "value": "test@test.com"}]}],
    }
    result = verify_action_result("type_text", old, new, True, "")
    assert result["verified"] is True
