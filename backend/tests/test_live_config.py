from google.genai import types

from app.live_config import (
    build_live_connect_config,
    live_settings_require_v1alpha,
    normalize_live_settings,
)


def test_normalize_live_settings_rejects_invalid_values():
    settings = normalize_live_settings(
        {
            "voiceName": "  Puck  ",
            "thinkingBudget": "1024",
            "allowInterruptions": "no",
            "enableInputTranscription": "yes",
            "enableOutputTranscription": False,
        }
    )

    assert settings["voiceName"] == "Puck"
    assert settings["thinkingBudget"] == 1024
    assert settings["allowInterruptions"] is True
    assert settings["enableInputTranscription"] is True
    assert settings["enableOutputTranscription"] is False
    assert settings == {
        "voiceName": "Puck",
        "thinkingBudget": 1024,
        "allowInterruptions": True,
        "enableInputTranscription": True,
        "enableOutputTranscription": False,
    }


def test_build_live_connect_config_for_manual_mode_matches_existing_behavior():
    config = build_live_connect_config(
        settings={},
        tools=[],
        system_instruction="Be concise.",
    )

    assert config.response_modalities == ["AUDIO"]
    assert isinstance(config.input_audio_transcription, types.AudioTranscriptionConfig)
    assert isinstance(config.output_audio_transcription, types.AudioTranscriptionConfig)
    assert config.generation_config.thinking_config.thinking_budget == -1
    assert config.realtime_input_config.automatic_activity_detection.disabled is True
    assert (
        config.realtime_input_config.activity_handling
        == types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
    )
    assert (
        config.realtime_input_config.turn_coverage
        == types.TurnCoverage.TURN_INCLUDES_ALL_INPUT
    )


def test_build_live_connect_config_applies_user_facing_settings():
    config = build_live_connect_config(
        settings={
            "voiceName": "Aoede",
            "thinkingBudget": 0,
            "allowInterruptions": False,
            "enableInputTranscription": False,
            "enableOutputTranscription": True,
        },
        tools=[],
        system_instruction="Be concise.",
    )

    assert config.input_audio_transcription is None
    assert isinstance(config.output_audio_transcription, types.AudioTranscriptionConfig)
    assert config.media_resolution is None
    assert config.generation_config.thinking_config.thinking_budget == 0
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Aoede"
    assert config.enable_affective_dialog is None
    assert config.proactivity is None
    assert config.realtime_input_config.automatic_activity_detection.disabled is True
    assert (
        config.realtime_input_config.activity_handling
        == types.ActivityHandling.NO_INTERRUPTION
    )


def test_live_settings_require_v1alpha_stays_disabled_for_current_settings():
    assert live_settings_require_v1alpha({}) is False
    assert live_settings_require_v1alpha({"voiceName": "Aoede"}) is False
