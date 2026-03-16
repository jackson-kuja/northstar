"""Utilities for normalizing Gemini Live transcript text."""

from __future__ import annotations

import re

_NOISE_TOKEN_PATTERN = re.compile(r"<\s*noise\s*>", flags=re.IGNORECASE)


def sanitize_live_transcript_text(text: str | None) -> str:
    """Strip non-speech transcript markers while preserving chunk-edge spacing."""
    value = str(text or "")
    if not value:
        return ""
    value = _NOISE_TOKEN_PATTERN.sub(" ", value)
    if not value.strip():
        return ""

    has_leading_space = value[:1].isspace()
    has_trailing_space = value[-1:].isspace()
    normalized = " ".join(value.split())
    if not normalized:
        return ""
    if has_leading_space:
        normalized = f" {normalized}"
    if has_trailing_space:
        normalized = f"{normalized} "
    return normalized
