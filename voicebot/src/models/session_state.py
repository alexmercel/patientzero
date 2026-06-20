"""Session state models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

from voicebot.src.models.call_metadata import CallMetadata


class SessionStatus(StrEnum):
    """High-level lifecycle states for a live call session."""

    CREATED = "created"
    CONNECTING = "connecting"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class TranscriptTurn(BaseModel):
    """Committed transcript turn for one completed utterance."""

    speaker: str
    text: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionState(BaseModel):
    """Mutable in-memory state for a live voice bridge session."""

    session_id: str = Field(default_factory=lambda: str(uuid4()))
    call_sid: str | None = None
    stream_sid: str | None = None
    status: SessionStatus = SessionStatus.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    connected_at: datetime | None = None
    ended_at: datetime | None = None
    last_error: str | None = None
    bytes_from_twilio: int = 0
    bytes_to_twilio: int = 0
    bytes_to_gemini: int = 0
    bytes_from_gemini: int = 0
    packets_from_twilio: int = 0
    packets_to_twilio: int = 0
    packets_to_gemini: int = 0
    packets_from_gemini: int = 0
    metadata: CallMetadata | None = None
    transcript_turns: list[TranscriptTurn] = Field(default_factory=list)

    @classmethod
    def from_metadata(cls, metadata: CallMetadata) -> "SessionState":
        """Create session state from durable call metadata."""
        return cls(
            call_sid=metadata.call_sid,
            status=SessionStatus.CONNECTING,
            metadata=metadata,
        )

    def attach_stream(self, stream_sid: str) -> None:
        """Attach the Twilio stream identifier."""
        self.stream_sid = stream_sid
        if self.metadata is not None:
            self.metadata.stream_sid = stream_sid
            self.metadata.touch()

    def mark_active(self) -> None:
        """Mark the session as actively bridging audio."""
        self.status = SessionStatus.ACTIVE
        self.connected_at = datetime.now(UTC)
        if self.metadata is not None:
            self.metadata.touch(status=self.status.value)

    def mark_completed(self) -> None:
        """Mark the session as completed."""
        self.status = SessionStatus.COMPLETED
        self.ended_at = datetime.now(UTC)
        if self.metadata is not None:
            self.metadata.touch(status=self.status.value)

    def mark_failed(self, message: str) -> None:
        """Mark the session as failed and persist the most recent error."""
        self.status = SessionStatus.FAILED
        self.last_error = message
        self.ended_at = datetime.now(UTC)
        if self.metadata is not None:
            self.metadata.touch(status=self.status.value)

    def record_twilio_inbound(self, byte_count: int) -> None:
        """Update counters for audio received from Twilio."""
        self.bytes_from_twilio += byte_count
        self.packets_from_twilio += 1

    def record_twilio_outbound(self, byte_count: int) -> None:
        """Update counters for audio sent to Twilio."""
        self.bytes_to_twilio += byte_count
        self.packets_to_twilio += 1

    def record_gemini_inbound(self, byte_count: int) -> None:
        """Update counters for audio received from Gemini."""
        self.bytes_from_gemini += byte_count
        self.packets_from_gemini += 1

    def record_gemini_outbound(self, byte_count: int) -> None:
        """Update counters for audio sent to Gemini."""
        self.bytes_to_gemini += byte_count
        self.packets_to_gemini += 1

    def append_transcript_turn(self, speaker: str, text: str) -> None:
        """Persist one committed transcript turn for later analysis."""
        cleaned = text.strip()
        if not cleaned:
            return
        self.transcript_turns.append(TranscriptTurn(speaker=speaker, text=cleaned))
