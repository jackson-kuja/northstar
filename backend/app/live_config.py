"""Gemini Live settings normalization and config construction."""

from __future__ import annotations

from typing import Any

from google.genai import types

LIVE_SETTINGS_DEFAULTS: dict[str, Any] = {
    "voiceName": "",
    "thinkingBudget": -1,
    "allowInterruptions": True,
    "enableInputTranscription": True,
    "enableOutputTranscription": True,
}
LIVE_THINKING_BUDGET_VALUES = frozenset((-1, 0, 1024))


def _normalize_bool(value: Any, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def _normalize_string(value: Any, *, max_length: int = 80) -> str:
    return str(value or "").strip()[:max_length]


def _normalize_int_choice(
    value: Any, fallback: int, allowed_values: frozenset[int]
) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed in allowed_values else fallback


def normalize_live_settings(raw_settings: dict[str, Any] | None) -> dict[str, Any]:
    raw_settings = raw_settings or {}
    return {
        "voiceName": _normalize_string(raw_settings.get("voiceName"), max_length=40),
        "thinkingBudget": _normalize_int_choice(
            raw_settings.get("thinkingBudget"),
            LIVE_SETTINGS_DEFAULTS["thinkingBudget"],
            LIVE_THINKING_BUDGET_VALUES,
        ),
        "allowInterruptions": _normalize_bool(
            raw_settings.get("allowInterruptions"),
            LIVE_SETTINGS_DEFAULTS["allowInterruptions"],
        ),
        "enableInputTranscription": _normalize_bool(
            raw_settings.get("enableInputTranscription"),
            LIVE_SETTINGS_DEFAULTS["enableInputTranscription"],
        ),
        "enableOutputTranscription": _normalize_bool(
            raw_settings.get("enableOutputTranscription"),
            LIVE_SETTINGS_DEFAULTS["enableOutputTranscription"],
        ),
    }


def live_settings_require_v1alpha(settings: dict[str, Any] | None) -> bool:
    normalize_live_settings(settings)
    return False


def build_live_connect_config(
    *,
    settings: dict[str, Any] | None,
    tools: list[types.Tool],
    system_instruction: str,
) -> types.LiveConnectConfig:
    normalized = normalize_live_settings(settings)

    config_kwargs: dict[str, Any] = {
        "response_modalities": ["AUDIO"],
        "system_instruction": system_instruction,
        "tools": tools,
    }

    if normalized["enableInputTranscription"]:
        config_kwargs["input_audio_transcription"] = types.AudioTranscriptionConfig()
    if normalized["enableOutputTranscription"]:
        config_kwargs["output_audio_transcription"] = types.AudioTranscriptionConfig()

    config_kwargs["generation_config"] = {
        "thinking_config": {"thinking_budget": normalized["thinkingBudget"]}
    }

    if normalized["voiceName"]:
        config_kwargs["speech_config"] = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=normalized["voiceName"]
                )
            )
        )

    config_kwargs["realtime_input_config"] = types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
        activity_handling=(
            types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
            if normalized["allowInterruptions"]
            else types.ActivityHandling.NO_INTERRUPTION
        ),
        turn_coverage=types.TurnCoverage.TURN_INCLUDES_ALL_INPUT,
    )

    return types.LiveConnectConfig(**config_kwargs)
