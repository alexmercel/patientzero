"""HTTP API request and response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OutboundCallRequest(BaseModel):
    """Request payload for outbound call creation."""

    to_number: str | None = None
    stream_base_url: str | None = None
    record_call: bool = True
    custom_parameters: dict[str, str] = Field(default_factory=dict)


class OutboundCallResponse(BaseModel):
    """Response payload after a call is queued."""

    call_sid: str
    status: str


class HangupCallResponse(BaseModel):
    """Response payload after ending a call."""

    call_sid: str
    status: str


class RecordingSummary(BaseModel):
    """Dashboard-facing summary of a stored recording."""

    call_sid: str
    recording_sid: str
    saved_at: str
    filename: str
    display_name: str | None = None
    media_url: str | None = None
    report_filename: str | None = None
    report_available: bool = False
    deep_analysis_filename: str | None = None
    deep_analysis_available: bool = False
    favorite: bool = False
    recording_path: str


class StartTestCallRequest(BaseModel):
    """Optional selection overrides for a test call."""

    persona_id: str | None = None
    scenario_ids: list[str] | None = None


class RecordingFavoriteRequest(BaseModel):
    """Optional favorite-toggle payload for a recording."""

    favorite: bool | None = None


class HealthResponse(BaseModel):
    """Basic health response."""

    status: str = "ok"
