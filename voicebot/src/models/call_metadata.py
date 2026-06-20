"""Call metadata models."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class CallMetadata(BaseModel):
    """Durable metadata for a Twilio call session."""

    call_sid: str
    to_number: str
    from_number: str
    stream_url: str
    recording_enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = "queued"
    stream_sid: str | None = None
    recording_sid: str | None = None
    recording_path: str | None = None
    custom_parameters: dict[str, str] = Field(default_factory=dict)

    def touch(self, status: str | None = None) -> None:
        """Update mutable timestamps and optional status."""
        self.updated_at = datetime.now(UTC)
        if status is not None:
            self.status = status
