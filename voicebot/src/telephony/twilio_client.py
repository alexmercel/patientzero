"""Thin Twilio SDK wrapper with logging and error handling."""

from __future__ import annotations

from typing import Any

from voicebot.src.config.settings import Settings
from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.utils.logging import get_logger

try:
    from twilio.rest import Client as TwilioSdkClient
    from twilio.twiml.voice_response import VoiceResponse
except ImportError:  # pragma: no cover - exercised through dependency guards
    TwilioSdkClient = None
    VoiceResponse = None


class TwilioClientError(RuntimeError):
    """Raised when the Twilio SDK fails."""


class TwilioClient:
    """Wrapper around the Twilio REST client."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self.logger = get_logger(__name__)
        self._client = client or self._build_client()

    def _build_client(self) -> Any:
        if TwilioSdkClient is None:
            raise TwilioClientError("twilio package is not installed.")
        return TwilioSdkClient(self.settings.twilio_account_sid, self.settings.twilio_auth_token)

    def validate_credentials(self) -> str:
        """Validate Twilio credentials by fetching the current account."""
        try:
            account = self._client.api.accounts(self.settings.twilio_account_sid).fetch()
            self.logger.info("twilio_credentials_validated", account_sid=account.sid)
            return account.sid
        except Exception as exc:  # pragma: no cover - network-dependent
            self.logger.exception("twilio_validate_credentials_failed")
            raise TwilioClientError("Failed to validate Twilio credentials.") from exc

    def build_media_stream_twiml(
        self,
        stream_url: str,
        custom_parameters: dict[str, str] | None = None,
        status_callback_url: str | None = None,
    ) -> str:
        """Build inline TwiML that connects the call to a media stream."""
        if VoiceResponse is None:
            raise TwilioClientError("twilio package is not installed.")

        response = VoiceResponse()
        connect = response.connect()
        stream_kwargs: dict[str, Any] = {"url": stream_url}
        if status_callback_url:
            stream_kwargs["status_callback"] = status_callback_url
            stream_kwargs["status_callback_method"] = "POST"
        stream = connect.stream(**stream_kwargs)
        for name, value in (custom_parameters or {}).items():
            if hasattr(stream, "parameter"):
                stream.parameter(name=name, value=value)
        return str(response)

    def initiate_outbound_call(
        self,
        to_number: str,
        stream_url: str,
        record_call: bool = True,
        custom_parameters: dict[str, str] | None = None,
        status_callback_url: str | None = None,
        recording_status_callback_url: str | None = None,
        stream_status_callback_url: str | None = None,
    ) -> CallMetadata:
        """Create an outbound call and return initial call metadata."""
        twiml = self.build_media_stream_twiml(
            stream_url,
            custom_parameters,
            status_callback_url=stream_status_callback_url,
        )
        create_kwargs: dict[str, Any] = {
            "to": to_number,
            "from_": self.settings.twilio_phone_number,
            "twiml": twiml,
            "record": record_call,
        }
        if status_callback_url:
            create_kwargs["status_callback"] = status_callback_url
            create_kwargs["status_callback_event"] = ["initiated", "ringing", "answered", "completed"]
        if recording_status_callback_url:
            create_kwargs["recording_status_callback"] = recording_status_callback_url

        try:
            self.logger.info(
                "twilio_call_initiating",
                to_number=to_number,
                stream_url=stream_url,
                record_call=record_call,
                status_callback_url=status_callback_url,
                recording_status_callback_url=recording_status_callback_url,
                stream_status_callback_url=stream_status_callback_url,
            )
            call = self._client.calls.create(**create_kwargs)
            self.logger.info("twilio_call_initiated", call_sid=call.sid, to_number=to_number)
            return CallMetadata(
                call_sid=call.sid,
                to_number=to_number,
                from_number=self.settings.twilio_phone_number,
                stream_url=stream_url,
                recording_enabled=record_call,
                status=getattr(call, "status", "queued"),
                custom_parameters=custom_parameters or {},
            )
        except Exception as exc:
            self.logger.exception("twilio_call_initiate_failed", to_number=to_number)
            raise TwilioClientError("Failed to initiate outbound Twilio call.") from exc

    def end_call(self, call_sid: str) -> str:
        """Force-complete an active Twilio call."""
        try:
            call = self._client.calls(call_sid).update(status="completed")
            status = getattr(call, "status", "completed")
            self.logger.info("twilio_call_completed", call_sid=call_sid, status=status)
            return status
        except Exception as exc:
            self.logger.exception("twilio_call_complete_failed", call_sid=call_sid)
            raise TwilioClientError("Failed to complete Twilio call.") from exc
