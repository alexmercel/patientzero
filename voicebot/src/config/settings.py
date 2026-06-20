"""Application settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    twilio_account_sid: str = Field(alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = Field(alias="TWILIO_AUTH_TOKEN")
    twilio_phone_number: str = Field(alias="TWILIO_PHONE_NUMBER")
    my_phone_number: str = Field(alias="MY_PHONE_NUMBER")
    gemini_api_key: str = Field(alias="GEMINI_API_KEY")

    public_base_url: str | None = Field(default=None, alias="PUBLIC_BASE_URL")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    gemini_model: str = Field(
        default="gemini-3.1-flash-live-preview",
        alias="GEMINI_MODEL",
    )
    gemini_voice_name: str = Field(default="Kore", alias="GEMINI_VOICE_NAME")
    gemini_system_instruction: str = Field(
        default=(
            "Role:\n"
            "You are simulating a patient who has called a help center and is having one continuous live phone conversation with the help-center representative.\n"
            "If no separate persona or scenario has been injected yet, use 'Patient Zero' only as a fallback name when the representative explicitly asks for your name.\n\n"
            "Continuity:\n"
            "- Stay in character as the patient for the entire call.\n"
            "- Do not act like an assistant, operator, receptionist, or agent.\n"
            "- Do not introduce yourself with a name unless the representative asks for it.\n"
            "- Answer identification questions only when asked, and keep the answer brief.\n"
            "- After interruptions or reconnects, continue from the last unresolved point without restarting the scenario or recapping unless asked.\n\n"
            "Interaction model:\n"
            "- Treat the other speaker as the help-center representative you called for help.\n"
            "- Speak from the patient's perspective using first person language.\n"
            "- Describe symptoms, needs, concerns, scheduling issues, insurance questions, or follow-up details naturally when relevant.\n"
            "- Let the representative lead identity verification and intake; do not volunteer every detail upfront.\n"
            "- If the representative asks a direct question, answer it plainly instead of switching roles or giving meta commentary.\n"
            "- Do not ask to be transferred to a human, support representative, supervisor, or different department unless the scenario is a real emergency or the representative explicitly cannot proceed safely.\n"
            "- If the representative suggests a transfer or says you need a support representative, first try to stay with the current agent by briefly asking whether they can still help or by calmly restating the same issue.\n"
            "- Do not switch to a different unrelated question just because the representative mentions a transfer or limitation.\n\n"
            "Turn-taking and patience:\n"
            "- After you ask about one issue, stay on that same issue until it is clearly answered, declined, or the representative asks you a necessary follow-up question.\n"
            "- Do not introduce a new question just because there is a short pause.\n"
            "- If the representative says they are checking, looking something up, pulling information up, or asks you to hold for a moment, treat that as an in-progress answer rather than a failure to respond.\n"
            "- In those moments, either wait silently or give one brief acknowledgment such as 'Okay' or 'Sure,' then wait for the substantive answer.\n"
            "- Assume the representative may answer in multiple parts with pauses in between. Wait for the full answer before changing topics.\n"
            "- Do not stack multiple new requests while the representative is still working on the current one.\n\n"
            "Information sufficiency:\n"
            "- If the representative already gave enough information for the patient to respond, answer directly.\n"
            "- If the representative gives a favorable, usable, or clearly correct answer to the current question, accept it and move on instead of re-asking the same thing.\n"
            "- Ask at most one short follow-up question only when a missing detail truly matters from the patient's point of view.\n"
            "- If the representative gives essentially the same answer twice in a row, acknowledge it briefly and move forward instead of asking for the same thing again.\n"
            "- If the representative only partially answered, ask at most one short clarifying follow-up about the missing piece instead of repeating the whole request.\n"
            "- Never repeat the same concern, answer, or question unless the representative seems not to have heard it.\n"
            "- Prefer natural conversational progress over filler or generic pleasantries.\n\n"
            "Style:\n"
            "- Sound natural, concise, and believable as a real patient on a phone call.\n"
            "- Do not over-explain or narrate hidden reasoning.\n"
            "- Do not use stock pleasantries like 'I'm doing well, thanks' unless the representative directly asked how you are in this turn.\n"
            "- Avoid repeating the same phrase, reassurance, or suggestion.\n"
            "- Each response should either answer the representative, provide one relevant detail, or ask one necessary patient-side follow-up question."
        ),
        alias="GEMINI_SYSTEM_INSTRUCTION",
    )
    gemini_temperature: float = Field(default=0.4, alias="GEMINI_TEMPERATURE")
    gemini_deep_analysis_model: str = Field(
        default="gemini-3-flash",
        alias="GEMINI_DEEP_ANALYSIS_MODEL",
    )
    gemini_deep_analysis_fallback_model: str = Field(
        default="gemini-3.1-flash-lite",
        alias="GEMINI_DEEP_ANALYSIS_FALLBACK_MODEL",
    )
    gemini_deep_analysis_enabled: bool = Field(
        default=True,
        alias="GEMINI_DEEP_ANALYSIS_ENABLED",
    )
    gemini_deep_analysis_timeout_seconds: float = Field(
        default=180.0,
        alias="GEMINI_DEEP_ANALYSIS_TIMEOUT_SECONDS",
    )
    gemini_deep_analysis_trim_lead_seconds: float = Field(
        default=3.0,
        alias="GEMINI_DEEP_ANALYSIS_TRIM_LEAD_SECONDS",
    )
    representative_turn_settle_seconds: float = Field(
        default=2.2,
        alias="REPRESENTATIVE_TURN_SETTLE_SECONDS",
    )
    representative_activity_amplitude_threshold: int = Field(
        default=120,
        alias="REPRESENTATIVE_ACTIVITY_AMPLITUDE_THRESHOLD",
    )
    twilio_barge_in_amplitude_threshold: int = Field(
        default=1400,
        alias="TWILIO_BARGE_IN_AMPLITUDE_THRESHOLD",
    )
    twilio_barge_in_min_consecutive_frames: int = Field(
        default=3,
        alias="TWILIO_BARGE_IN_MIN_CONSECUTIVE_FRAMES",
    )
    twilio_websocket_path: str = "/ws/twilio-media"
    twilio_status_callback_path: str = "/twilio/status"
    twilio_recording_status_callback_path: str = "/twilio/recording-status"
    twilio_stream_status_callback_path: str = "/twilio/stream-status"
    request_timeout_seconds: float = 20.0
    gemini_max_reconnect_attempts: int = 3
    gemini_reconnect_backoff_seconds: float = 1.0
    recordings_dir: Path = Path("voicebot/recordings")
    logs_dir: Path = Path("voicebot/logs")
    test_scenarios_path: Path = Path("voicebot/data/master_scenarios.yaml")
    test_personas_path: Path = Path("voicebot/data/test_personas.yaml")
    test_report_path: Path = Path("voicebot/logs/testing_report.json")
    testing_demo_address: str = "1231 S California Blvd, Walnut Creek, CA 94596"
    testing_max_scenarios_per_call: int = 12
    testing_max_scenarios_per_category: int = 2

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator(
        "twilio_account_sid",
        "twilio_auth_token",
        "twilio_phone_number",
        "my_phone_number",
        "gemini_api_key",
    )
    @classmethod
    def validate_required_string(cls, value: str) -> str:
        """Reject empty secrets and phone numbers."""
        if not value or not value.strip():
            raise ValueError("value must not be empty")
        return value.strip()

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str | None) -> str | None:
        """Validate the optional externally reachable base URL."""
        if value is None:
            return None

        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("PUBLIC_BASE_URL must use http or https")
        if not parsed.netloc:
            raise ValueError("PUBLIC_BASE_URL must include a hostname")
        return value.rstrip("/")

    def ensure_directories(self) -> None:
        """Create local writable directories used by the application."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.test_report_path.parent.mkdir(parents=True, exist_ok=True)

    def build_public_url(self, path: str, base_url: str | None = None) -> str:
        """Build a public callback URL from a base host and route path."""
        root = (base_url or self.public_base_url or "").rstrip("/")
        if not root:
            raise ValueError("A public base URL is required for this operation.")
        return f"{root}{path}"

    def build_stream_url(self, base_url: str | None = None) -> str:
        """Build the Twilio WebSocket URL from the configured public host."""
        http_url = self.build_public_url(self.twilio_websocket_path, base_url)
        parsed = urlparse(http_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return parsed._replace(scheme=scheme).geturl()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached validated settings."""
    return Settings()


def validate_settings() -> Settings:
    """Explicit validation entrypoint for tests and startup checks."""
    try:
        return get_settings()
    except ValidationError:
        raise
