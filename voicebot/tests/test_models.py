from __future__ import annotations

from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.models.session_state import SessionState, SessionStatus


def test_session_state_tracks_status_from_metadata() -> None:
    metadata = CallMetadata(
        call_sid="CA123",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
    )
    session = SessionState.from_metadata(metadata)
    session.attach_stream("MZ123")
    session.mark_active()

    assert session.status == SessionStatus.ACTIVE
    assert session.stream_sid == "MZ123"
    assert session.metadata is metadata
