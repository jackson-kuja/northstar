"""Page map builder — converts raw DOM accessibility data into structured prompts."""

import json


def build_page_map_prompt(page_state: dict) -> str:
    """Build a structured page description from the page state sent by the content script."""
    parts = []

    url = page_state.get("url", "Unknown")
    title = page_state.get("title", "Unknown")
    parts.append(f"Page: {title}")
    parts.append(f"URL: {url}")
    parts.append(
        'DOM tool rule: when a page-map item includes selector="...", pass only that selector to click, type_text, read_element, or highlight.'
    )

    # Landmarks
    landmarks = page_state.get("landmarks", [])
    if landmarks:
        parts.append("\n## Landmarks")
        for lm in landmarks:
            label = lm.get("label", lm.get("role", "region"))
            parts.append(f"  - {label}")

    # Headings
    headings = page_state.get("headings", [])
    if headings:
        parts.append("\n## Headings")
        for h in headings:
            level = h.get("level", "?")
            text = h.get("text", "")
            parts.append(f"  - H{level}: {text}")

    # Forms
    forms = page_state.get("forms", [])
    if forms:
        parts.append("\n## Forms")
        for form in forms:
            name = form.get("name", "Unnamed form")
            fields = form.get("fields", [])
            parts.append(f"  Form: {name}")
            for field in fields:
                ftype = field.get("type", "text")
                flabel = field.get("label", "Unlabeled")
                fvalue = field.get("value", "")
                freq = "required" if field.get("required") else "optional"
                selector = field.get("selector", "")
                parts.append(
                    f'    - selector="{selector}" type="{ftype}" label="{flabel}" required="{freq}" value="{fvalue}"'
                )

    # Interactive elements
    interactives = page_state.get("interactives", [])
    if interactives:
        parts.append("\n## Interactive Elements")
        for el in interactives:
            tag = el.get("tag", "?")
            role = el.get("role", "")
            text = el.get("text", "")
            selector = el.get("selector", "")
            aria_label = el.get("ariaLabel", "")
            display_text = aria_label or text or "(no label)"
            issues = el.get("issues", [])
            issue_str = f" ⚠ {', '.join(issues)}" if issues else ""
            parts.append(
                f'  - selector="{selector}" tag="{tag}" role="{role}" label="{display_text}"{issue_str}'
            )

    # Images
    images = page_state.get("images", [])
    if images:
        missing_alt = [img for img in images if not img.get("alt")]
        if missing_alt:
            parts.append(f"\n## Images: {len(images)} total, {len(missing_alt)} missing alt text")

    # Focus info
    focus = page_state.get("focusedElement")
    if focus:
        parts.append(
            f'\n## Current Focus: selector="{focus.get("selector", "")}" tag="{focus.get("tag", "?")}" label="{focus.get("text", "")}"'
        )

    # Accessibility issues detected by content script
    issues = page_state.get("accessibilityIssues", [])
    if issues:
        parts.append("\n## Detected Accessibility Issues")
        for issue in issues:
            severity = issue.get("severity", "warning")
            desc = issue.get("description", "")
            element = issue.get("element", "")
            parts.append(f"  - [{severity}] {desc} ({element})")

    # Live regions
    live_regions = page_state.get("liveRegions", [])
    if live_regions:
        parts.append("\n## Live Regions")
        for lr in live_regions:
            parts.append(f"  - {lr.get('text', '')} (politeness: {lr.get('politeness', 'polite')})")

    return "\n".join(parts)


def extract_actionable_targets(page_state: dict) -> list[dict]:
    """Extract all actionable targets with their selectors and descriptions."""
    targets = []

    for el in page_state.get("interactives", []):
        targets.append({
            "selector": el.get("selector", ""),
            "description": el.get("ariaLabel") or el.get("text") or el.get("tag", "unknown"),
            "tag": el.get("tag", ""),
            "role": el.get("role", ""),
            "type": "interactive",
            "issues": el.get("issues", []),
        })

    for form in page_state.get("forms", []):
        for field in form.get("fields", []):
            targets.append({
                "selector": field.get("selector", ""),
                "description": field.get("label", "Unlabeled field"),
                "tag": "input",
                "role": field.get("type", "textbox"),
                "type": "form_field",
                "issues": [],
            })

    return targets
