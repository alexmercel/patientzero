from __future__ import annotations

from voicebot.src.observability.event_bus import DashboardEventBus


def test_begin_call_resets_transcript_and_scopes_future_updates() -> None:
    bus = DashboardEventBus()

    bus.begin_call("CA111")
    bus.publish(
        "log",
        {
            "event": "call_transcript_updated",
            "call_sid": "CA111",
            "speaker": "agent",
            "text": "Hello there",
            "level": "info",
        },
    )
    assert bus.transcript_messages()[0]["text"] == "Hello there"

    reset_event = bus.begin_call("CA222")
    assert reset_event is not None
    assert reset_event["payload"]["event"] == "transcript_reset"
    assert bus.transcript_messages() == []

    bus.publish(
        "log",
        {
            "event": "call_transcript_updated",
            "call_sid": "CA111",
            "speaker": "agent",
            "text": "Old call should not render",
            "level": "info",
        },
    )
    assert bus.transcript_messages() == []

    bus.publish(
        "log",
        {
            "event": "call_transcript_updated",
            "call_sid": "CA222",
            "speaker": "agent",
            "text": "Fresh call transcript",
            "level": "info",
        },
    )
    assert bus.transcript_messages()[0]["call_sid"] == "CA222"


def test_end_call_clears_transcript_and_blocks_late_updates() -> None:
    bus = DashboardEventBus()

    bus.begin_call("CA333")
    bus.publish(
        "log",
        {
            "event": "call_transcript_committed",
            "call_sid": "CA333",
            "speaker": "user",
            "text": "I need help",
            "level": "info",
        },
    )
    assert len(bus.transcript_messages()) == 1

    reset_event = bus.end_call("CA333")
    assert reset_event is not None
    assert reset_event["payload"]["reason"] == "call_ended"
    assert bus.transcript_messages() == []

    bus.publish(
        "log",
        {
            "event": "call_transcript_updated",
            "call_sid": "CA333",
            "speaker": "agent",
            "text": "late fragment",
            "level": "info",
        },
    )
    assert bus.transcript_messages() == []
