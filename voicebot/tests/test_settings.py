from __future__ import annotations

import pytest
from pydantic import ValidationError

from voicebot.src.config.settings import Settings


def test_settings_require_all_core_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "MY_PHONE_NUMBER",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    try:
        Settings(_env_file=None)
    except ValidationError as exc:
        missing = {error["loc"][0] for error in exc.errors()}
        assert "TWILIO_ACCOUNT_SID" in missing
        assert "TWILIO_AUTH_TOKEN" in missing
        assert "TWILIO_PHONE_NUMBER" in missing
        assert "MY_PHONE_NUMBER" in missing
        assert "GEMINI_API_KEY" in missing
    else:  # pragma: no cover
        raise AssertionError("Expected settings validation to fail.")


def test_settings_build_stream_url(settings: Settings) -> None:
    assert settings.build_stream_url() == "wss://example.test/ws/twilio-media"


def test_settings_build_public_url_requires_base(settings: Settings) -> None:
    settings.public_base_url = None
    try:
        settings.build_public_url("/twilio/status")
    except ValueError as exc:
        assert "public base URL" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected build_public_url to fail without a base URL.")


def test_settings_default_system_instruction_has_call_continuity_rules(settings: Settings) -> None:
    assert "simulating a patient" in settings.gemini_system_instruction
    assert "fallback name when the representative explicitly asks for your name" in settings.gemini_system_instruction
    assert "Do not introduce yourself with a name unless the representative asks for it" in settings.gemini_system_instruction
    assert "Treat the other speaker as the help-center representative" in settings.gemini_system_instruction
    assert "If the representative already gave enough information" in settings.gemini_system_instruction
    assert "Do not introduce a new question just because there is a short pause" in settings.gemini_system_instruction
    assert "gives a favorable, usable, or clearly correct answer" in settings.gemini_system_instruction
    assert "Do not ask to be transferred to a human" in settings.gemini_system_instruction
    assert "Do not switch to a different unrelated question" in settings.gemini_system_instruction
    assert "do not repeat the same full sentence again" in settings.gemini_system_instruction
    assert "give one detail at a time" in settings.gemini_system_instruction
    assert "do not keep pushing just to force a successful result" in settings.gemini_system_instruction


def test_settings_default_deep_analysis_model_is_flash(settings: Settings) -> None:
    assert settings.gemini_deep_analysis_model == "gemini-3-flash"


def test_settings_default_deep_analysis_fallback_model_is_flash_lite(settings: Settings) -> None:
    assert settings.gemini_deep_analysis_fallback_model == "gemini-3.1-flash-lite"


def test_settings_default_deep_analysis_trim_lead_seconds(settings: Settings) -> None:
    assert Settings.model_fields["gemini_deep_analysis_trim_lead_seconds"].default == 3.0


def test_settings_default_representative_turn_settle_seconds(settings: Settings) -> None:
    assert Settings.model_fields["representative_turn_settle_seconds"].default == 3.2
