"""Action planner — determines the best execution strategy using the confidence ladder."""

from enum import IntEnum


class ActionTier(IntEnum):
    SEMANTIC = 1       # Direct DOM/ARIA action via content script
    SEMANTIC_VISUAL = 2  # Semantic + visual disambiguation
    VISUAL_FALLBACK = 3  # Full visual computer-use
    ASK_USER = 4        # Insufficient confidence, ask user


def plan_actions(action: dict, page_state: dict) -> dict:
    """Determine execution tier and enrich action with execution details.

    Returns the action with an added 'tier' and 'execution' field.
    """
    target = action.get("args", {}).get("target", "")
    interactives = page_state.get("interactives", [])
    form_fields = []
    for form in page_state.get("forms", []):
        form_fields.extend(form.get("fields", []))

    # Try to find exact matches in the page map
    exact_matches = []
    partial_matches = []

    all_elements = interactives + [
        {"selector": f.get("selector", ""), "text": f.get("label", ""), "ariaLabel": f.get("label", ""), "tag": "input"}
        for f in form_fields
    ]

    target_lower = target.lower().strip()

    for el in all_elements:
        selector = el.get("selector", "")
        text = (el.get("text", "") or "").lower()
        aria = (el.get("ariaLabel", "") or "").lower()

        # Check if target is a CSS selector
        if target == selector:
            exact_matches.append(el)
            continue

        # Check text/aria match
        if target_lower == text or target_lower == aria:
            exact_matches.append(el)
        elif target_lower in text or target_lower in aria:
            partial_matches.append(el)

    if len(exact_matches) == 1:
        action["tier"] = ActionTier.SEMANTIC
        action["execution"] = {
            "method": "content_script",
            "selector": exact_matches[0].get("selector", target),
            "confidence": "high",
        }
    elif len(exact_matches) > 1:
        action["tier"] = ActionTier.SEMANTIC_VISUAL
        action["execution"] = {
            "method": "visual_disambiguation",
            "candidates": [e.get("selector", "") for e in exact_matches],
            "confidence": "medium",
        }
    elif len(partial_matches) == 1:
        action["tier"] = ActionTier.SEMANTIC
        action["execution"] = {
            "method": "content_script",
            "selector": partial_matches[0].get("selector", target),
            "confidence": "medium",
        }
    elif len(partial_matches) > 1:
        action["tier"] = ActionTier.SEMANTIC_VISUAL
        action["execution"] = {
            "method": "visual_disambiguation",
            "candidates": [e.get("selector", "") for e in partial_matches],
            "confidence": "low",
        }
    else:
        # No DOM match — use visual fallback or ask user
        action["tier"] = ActionTier.VISUAL_FALLBACK
        action["execution"] = {
            "method": "visual_coordinates",
            "target_description": target,
            "confidence": "low",
        }

    return action
