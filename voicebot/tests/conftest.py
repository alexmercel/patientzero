"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from voicebot.src.config.settings import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    recordings_dir = tmp_path / "recordings"
    logs_dir = tmp_path / "logs"
    return Settings(
        twilio_account_sid="AC123",
        twilio_auth_token="secret",
        twilio_phone_number="+15555550123",
        my_phone_number="+15555550124",
        gemini_api_key="gemini-secret",
        public_base_url="https://example.test",
        recordings_dir=recordings_dir,
        logs_dir=logs_dir,
        test_report_path=logs_dir / "testing_report.json",
        test_scenarios_path=Path("voicebot/data/master_scenarios.yaml"),
        test_personas_path=Path("voicebot/data/test_personas.yaml"),
    )
