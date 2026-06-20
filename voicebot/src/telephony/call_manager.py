"""In-memory call lifecycle tracking."""

from __future__ import annotations

from voicebot.src.config.settings import Settings
from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.models.session_state import SessionState, SessionStatus
from voicebot.src.telephony.twilio_client import TwilioClient
from voicebot.src.utils.logging import get_logger


class CallManager:
    """Tracks active calls and their session state."""

    def __init__(self, settings: Settings, twilio_client: TwilioClient) -> None:
        self.settings = settings
        self.twilio_client = twilio_client
        self.logger = get_logger(__name__)
        self.active_calls: dict[str, SessionState] = {}
        self.call_history: dict[str, SessionState] = {}

    def create_outbound_call(
        self,
        to_number: str | None = None,
        stream_base_url: str | None = None,
        record_call: bool = True,
        custom_parameters: dict[str, str] | None = None,
    ) -> CallMetadata:
        """Create a Twilio call and begin tracking its session state."""
        target_number = to_number or self.settings.my_phone_number
        stream_url = self.settings.build_stream_url(stream_base_url)
        status_callback_url = self._optional_public_url(self.settings.twilio_status_callback_path, stream_base_url)
        recording_status_callback_url = self._optional_public_url(
            self.settings.twilio_recording_status_callback_path,
            stream_base_url,
        )
        stream_status_callback_url = self._optional_public_url(
            self.settings.twilio_stream_status_callback_path,
            stream_base_url,
        )
        metadata = self.twilio_client.initiate_outbound_call(
            to_number=target_number,
            stream_url=stream_url,
            record_call=record_call,
            custom_parameters=custom_parameters,
            status_callback_url=status_callback_url,
            recording_status_callback_url=recording_status_callback_url,
            stream_status_callback_url=stream_status_callback_url,
        )
        session = SessionState.from_metadata(metadata)
        self.active_calls[metadata.call_sid] = session
        self.call_history[metadata.call_sid] = session
        self.logger.info(
            "call_manager_call_registered",
            call_sid=metadata.call_sid,
            to_number=metadata.to_number,
        )
        return metadata

    def get(self, call_sid: str) -> SessionState | None:
        """Return active or historical session state."""
        return self.active_calls.get(call_sid) or self.call_history.get(call_sid)

    def get_or_create(self, call_sid: str, metadata: CallMetadata | None = None) -> SessionState:
        """Return an existing session or create a placeholder session."""
        existing = self.get(call_sid)
        if existing is not None:
            return existing

        session = SessionState.from_metadata(metadata) if metadata else SessionState(call_sid=call_sid)
        self.active_calls[call_sid] = session
        self.call_history[call_sid] = session
        self.logger.info("call_manager_placeholder_session_created", call_sid=call_sid)
        return session

    def attach_stream(self, call_sid: str, stream_sid: str) -> SessionState:
        """Attach the Twilio stream identifier and activate the session."""
        session = self.get_or_create(call_sid)
        session.attach_stream(stream_sid)
        session.mark_active()
        self.logger.info("call_manager_stream_attached", call_sid=call_sid, stream_sid=stream_sid)
        return session

    def update_call_status(self, call_sid: str, status: str) -> SessionState:
        """Persist Twilio call status updates."""
        session = self.get_or_create(call_sid)
        if session.metadata is not None:
            session.metadata.touch(status=status)
        if status == "completed":
            session.mark_completed()
            self.active_calls.pop(call_sid, None)
        self.logger.info("call_manager_status_updated", call_sid=call_sid, status=status)
        return session

    def record_failure(self, call_sid: str, message: str) -> SessionState:
        """Mark a call as failed and remove it from active tracking."""
        session = self.get_or_create(call_sid)
        session.mark_failed(message)
        self.active_calls.pop(call_sid, None)
        self.logger.error("call_manager_call_failed", call_sid=call_sid, error=message)
        return session

    def complete_call(self, call_sid: str) -> SessionState:
        """Complete and clean up an active call."""
        session = self.get_or_create(call_sid)
        session.mark_completed()
        self.active_calls.pop(call_sid, None)
        self.logger.info("call_manager_call_completed", call_sid=call_sid)
        return session

    def hang_up_call(self, call_sid: str) -> SessionState:
        """End a live Twilio call and update local state."""
        session = self.get(call_sid)
        if session is None:
            raise ValueError(f"Unknown call SID: {call_sid}")

        if session.status == SessionStatus.COMPLETED:
            return session

        remote_status = self.twilio_client.end_call(call_sid)
        if session.metadata is not None:
            session.metadata.touch(status=remote_status)
        self.logger.info(
            "call_manager_hangup_requested",
            call_sid=call_sid,
            remote_status=remote_status,
        )
        return self.complete_call(call_sid)

    def list_calls(self, active_only: bool = False) -> list[SessionState]:
        """Return calls ordered from most recent to oldest."""
        sessions = self.active_calls.values() if active_only else self.call_history.values()
        return sorted(
            sessions,
            key=lambda session: (
                session.metadata.updated_at if session.metadata is not None else session.created_at
            ),
            reverse=True,
        )

    def _optional_public_url(self, path: str, base_url: str | None) -> str | None:
        try:
            return self.settings.build_public_url(path, base_url)
        except ValueError:
            return None
