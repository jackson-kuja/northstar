"""Post-action verification engine — confirms that intended state changes occurred."""


def verify_action_result(
    action_name: str,
    old_state: dict,
    new_state: dict,
    success: bool,
    error: str,
) -> dict:
    """Verify that an action produced the expected result.

    Returns a dict with 'verified' (bool) and 'narration' (str).
    """
    if not success:
        return {
            "verified": False,
            "narration": f"The action '{action_name}' failed: {error}. Let me try an alternative approach.",
        }

    old_url = old_state.get("url", "")
    new_url = new_state.get("url", "")

    # Navigation verification
    if action_name == "navigate" or action_name == "click":
        if old_url != new_url:
            new_title = new_state.get("title", "unknown page")
            return {
                "verified": True,
                "narration": f"The page navigated to {new_title}.",
            }

    # Click verification
    if action_name == "click":
        old_interactives = len(old_state.get("interactives", []))
        new_interactives = len(new_state.get("interactives", []))

        # Check for modal/dialog appearance
        new_landmarks = [l.get("role", "") for l in new_state.get("landmarks", [])]
        if "dialog" in new_landmarks:
            return {
                "verified": True,
                "narration": "A dialog appeared on the page.",
            }

        # Check for state change in focused element
        new_focus = new_state.get("focusedElement", {})
        if new_focus:
            return {
                "verified": True,
                "narration": f"Clicked successfully. Focus is now on {new_focus.get('text', 'an element')}.",
            }

        # Generic success
        if old_interactives != new_interactives:
            return {
                "verified": True,
                "narration": "The page updated after the click.",
            }

    # Type verification
    if action_name == "type_text":
        new_focus = new_state.get("focusedElement", {})
        for form in new_state.get("forms", []):
            for field in form.get("fields", []):
                if field.get("value"):
                    return {
                        "verified": True,
                        "narration": f"Text entered into {field.get('label', 'the field')}.",
                    }

    # Scroll verification
    if action_name == "scroll":
        old_scroll = old_state.get("scrollPosition", {})
        new_scroll = new_state.get("scrollPosition", {})
        if old_scroll != new_scroll:
            return {
                "verified": True,
                "narration": "Scrolled the page.",
            }
        return {
            "verified": False,
            "narration": "The page did not appear to scroll. Let me try a different approach.",
        }

    # Read-only or informational actions can trust the success flag directly.
    if action_name in {"read_element", "get_page_map", "highlight", "diagnose_accessibility"} and success:
        return {
            "verified": True,
            "narration": f"Action '{action_name}' completed.",
        }

    # For mutating actions, prefer explicit evidence of change.
    if action_name in {"click", "type_text", "navigate"}:
        return {
            "verified": False,
            "narration": f"Could not verify that '{action_name}' changed the page. Let me try another approach.",
        }

    if success:
        return {
            "verified": True,
            "narration": f"Action '{action_name}' completed.",
        }

    return {
        "verified": False,
        "narration": f"Could not verify that '{action_name}' had the intended effect. Let me check the page again.",
    }
