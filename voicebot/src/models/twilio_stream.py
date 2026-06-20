"""Twilio media stream message models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TwilioMediaFormat(BaseModel):
    """Audio format metadata from Twilio start events."""

    encoding: str
    sample_rate: int = Field(alias="sampleRate")
    channels: int

    model_config = ConfigDict(populate_by_name=True)


class TwilioStartPayload(BaseModel):
    """`start` event payload."""

    account_sid: str = Field(alias="accountSid")
    call_sid: str = Field(alias="callSid")
    stream_sid: str = Field(alias="streamSid")
    tracks: list[str]
    media_format: TwilioMediaFormat = Field(alias="mediaFormat")
    custom_parameters: dict[str, str] = Field(default_factory=dict, alias="customParameters")

    model_config = ConfigDict(populate_by_name=True)


class TwilioMediaPayload(BaseModel):
    """`media` event payload."""

    track: str | None = None
    chunk: str | None = None
    timestamp: str | None = None
    payload: str


class TwilioStopPayload(BaseModel):
    """`stop` event payload."""

    account_sid: str = Field(alias="accountSid")
    call_sid: str = Field(alias="callSid")

    model_config = ConfigDict(populate_by_name=True)


class TwilioStartEvent(BaseModel):
    """Twilio websocket start message."""

    event: str
    sequence_number: str | None = Field(default=None, alias="sequenceNumber")
    start: TwilioStartPayload
    stream_sid: str = Field(alias="streamSid")

    model_config = ConfigDict(populate_by_name=True)


class TwilioMediaEvent(BaseModel):
    """Twilio websocket media message."""

    event: str
    sequence_number: str | None = Field(default=None, alias="sequenceNumber")
    media: TwilioMediaPayload
    stream_sid: str = Field(alias="streamSid")

    model_config = ConfigDict(populate_by_name=True)


class TwilioStopEvent(BaseModel):
    """Twilio websocket stop message."""

    event: str
    sequence_number: str | None = Field(default=None, alias="sequenceNumber")
    stop: TwilioStopPayload
    stream_sid: str = Field(alias="streamSid")

    model_config = ConfigDict(populate_by_name=True)


class TwilioMarkPayload(BaseModel):
    """`mark` event payload."""

    name: str


class TwilioMarkEvent(BaseModel):
    """Twilio websocket mark message."""

    event: str
    sequence_number: str | None = Field(default=None, alias="sequenceNumber")
    mark: TwilioMarkPayload
    stream_sid: str = Field(alias="streamSid")

    model_config = ConfigDict(populate_by_name=True)


class TwilioOutboundMediaPayload(BaseModel):
    """Outbound payload sent back to Twilio."""

    payload: str


class TwilioOutboundMediaMessage(BaseModel):
    """Bidirectional media message sent to Twilio."""

    event: str = "media"
    stream_sid: str = Field(alias="streamSid")
    media: TwilioOutboundMediaPayload

    model_config = ConfigDict(populate_by_name=True)


class TwilioOutboundMarkPayload(BaseModel):
    """Outbound mark payload sent back to Twilio."""

    name: str


class TwilioOutboundMarkMessage(BaseModel):
    """Bidirectional mark message sent to Twilio."""

    event: str = "mark"
    stream_sid: str = Field(alias="streamSid")
    mark: TwilioOutboundMarkPayload

    model_config = ConfigDict(populate_by_name=True)


class TwilioOutboundClearMessage(BaseModel):
    """Bidirectional clear message sent to Twilio."""

    event: str = "clear"
    stream_sid: str = Field(alias="streamSid")

    model_config = ConfigDict(populate_by_name=True)
