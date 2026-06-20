from __future__ import annotations

from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.models.session_state import SessionStatus
from voicebot.src.telephony.call_manager import CallManager


class FakeTwilioClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.ended_calls: list[str] = []

    def initiate_outbound_call(self, **kwargs):
        self.calls.append(kwargs)
        return CallMetadata(
            call_sid="CA123",
            to_number=str(kwargs["to_number"]),
            from_number="+15555550123",
            stream_url=str(kwargs["stream_url"]),
            recording_enabled=bool(kwargs["record_call"]),
        )

    def end_call(self, call_sid: str) -> str:
        self.ended_calls.append(call_sid)
        return "completed"


def test_call_manager_tracks_active_calls(settings) -> None:
    manager = CallManager(settings, FakeTwilioClient())
    metadata = manager.create_outbound_call()
    session = manager.attach_stream(metadata.call_sid, "MZ123")

    assert metadata.call_sid in manager.active_calls
    assert session.status == SessionStatus.ACTIVE
    assert session.stream_sid == "MZ123"


def test_call_manager_completes_and_cleans_up(settings) -> None:
    manager = CallManager(settings, FakeTwilioClient())
    metadata = manager.create_outbound_call()
    manager.complete_call(metadata.call_sid)

    assert metadata.call_sid not in manager.active_calls
    assert manager.call_history[metadata.call_sid].status == SessionStatus.COMPLETED


def test_call_manager_hangs_up_active_call(settings) -> None:
    twilio_client = FakeTwilioClient()
    manager = CallManager(settings, twilio_client)
    metadata = manager.create_outbound_call()
    manager.attach_stream(metadata.call_sid, "MZ123")

    session = manager.hang_up_call(metadata.call_sid)

    assert session.status == SessionStatus.COMPLETED
    assert metadata.call_sid not in manager.active_calls
    assert twilio_client.ended_calls == ["CA123"]
