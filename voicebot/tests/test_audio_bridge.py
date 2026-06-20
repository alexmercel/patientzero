from __future__ import annotations

import asyncio
from time import monotonic

import pytest

from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.models.session_state import SessionState
from voicebot.src.models.twilio_stream import TwilioMarkEvent, TwilioMediaEvent, TwilioStartEvent
from voicebot.src.realtime.audio_bridge import AudioBridge, _merge_transcript
from voicebot.src.realtime.gemini_live_client import GeminiLiveEvent
from voicebot.src.utils.audio import encode_base64_payload, pcm16le_to_ulaw_bytes


class FakeGeminiClient:
    def __init__(self, events):
        self.events = list(events)
        self.connected = False
        self.disconnected = False
        self.sent_audio = []
        self.sent_text_instructions = []
        self.user_turns = []
        self.agent_turns = []
        self.call_instructions = []

    async def connect(self) -> None:
        self.connected = True

    def bind_context(self, **context) -> None:
        return None

    async def disconnect(self) -> None:
        self.disconnected = True

    async def send_audio(self, pcm_bytes: bytes, sample_rate: int) -> None:
        self.sent_audio.append((pcm_bytes, sample_rate))

    async def send_text_instruction(self, text: str) -> None:
        self.sent_text_instructions.append(text)

    async def receive_events(self):
        for event in self.events:
            yield event

    def remember_user_turn(self, text: str) -> None:
        self.user_turns.append(text)

    def remember_agent_turn(self, text: str) -> None:
        self.agent_turns.append(text)

    def set_call_instruction(self, instruction: str | None) -> None:
        self.call_instructions.append(instruction)


class FakeTestCampaignManager:
    def __init__(self) -> None:
        self.updated_sessions = []
        self.transition_nudges = []

    def update_live_progress(self, session) -> None:
        self.updated_sessions.append(session.call_sid)

    def process_live_turns(self, session) -> str | None:
        if self.transition_nudges:
            return self.transition_nudges.pop(0)
        return None

    def build_call_instruction(self, call_sid: str) -> str:
        return f"instruction for {call_sid}"


def _build_start_event() -> TwilioStartEvent:
    return TwilioStartEvent.model_validate(
        {
            "event": "start",
            "streamSid": "MZ123",
            "start": {
                "accountSid": "AC123",
                "callSid": "CA123",
                "streamSid": "MZ123",
                "tracks": ["inbound"],
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                "customParameters": {},
            },
        }
    )


def _build_media_event() -> TwilioMediaEvent:
    pcm = (200).to_bytes(2, "little", signed=True) * 8
    ulaw = pcm16le_to_ulaw_bytes(pcm)
    return TwilioMediaEvent.model_validate(
        {
            "event": "media",
            "streamSid": "MZ123",
            "media": {"payload": encode_base64_payload(ulaw)},
        }
    )


def _build_mark_event(name: str) -> TwilioMarkEvent:
    return TwilioMarkEvent.model_validate(
        {
            "event": "mark",
            "streamSid": "MZ123",
            "mark": {"name": name},
        }
    )


@pytest.mark.asyncio
async def test_audio_bridge_forwards_audio_both_directions(settings) -> None:
    sent_messages = []

    async def send_message(message) -> None:
        sent_messages.append(message)

    session = SessionState.from_metadata(
        CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
    )
    gemini = FakeGeminiClient([GeminiLiveEvent(audio=(100).to_bytes(2, "little", signed=True) * 240)])
    bridge = AudioBridge(session, settings, gemini, send_message)

    await bridge.start(_build_start_event())
    await bridge.handle_twilio_media(_build_media_event())
    await asyncio.sleep(0)

    assert gemini.connected is True
    assert gemini.sent_audio
    forwarded_audio, forwarded_rate = gemini.sent_audio[0]
    assert forwarded_rate == 16000
    assert len(forwarded_audio) > 16
    assert sent_messages
    assert any(message.event == "media" for message in sent_messages)
    assert any(message.event == "mark" for message in sent_messages)

    await bridge.close()
    assert gemini.disconnected is True


@pytest.mark.asyncio
async def test_audio_bridge_suppresses_twilio_audio_during_agent_playback(settings) -> None:
    sent_messages = []

    async def send_message(message) -> None:
        sent_messages.append(message)

    session = SessionState.from_metadata(
        CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
    )
    gemini = FakeGeminiClient([])
    bridge = AudioBridge(session, settings, gemini, send_message)

    await bridge.start(_build_start_event())
    bridge._suppress_input_until = monotonic() + 60.0
    await bridge.handle_twilio_media(_build_media_event())

    assert gemini.sent_audio == []
    assert sent_messages == []

    await bridge.close()


@pytest.mark.asyncio
async def test_audio_bridge_drops_interrupted_agent_turn(settings) -> None:
    sent_messages = []

    async def send_message(message) -> None:
        sent_messages.append(message)

    session = SessionState.from_metadata(
        CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
    )
    gemini = FakeGeminiClient(
        [
            GeminiLiveEvent(transcript="Let me think about that"),
            GeminiLiveEvent(interrupted=True),
        ]
    )
    bridge = AudioBridge(session, settings, gemini, send_message)

    await bridge.start(_build_start_event())
    await asyncio.sleep(0)

    assert gemini.agent_turns == []

    await bridge.close()


@pytest.mark.asyncio
async def test_audio_bridge_clears_twilio_playback_on_interruption(settings) -> None:
    sent_messages = []

    async def send_message(message) -> None:
        sent_messages.append(message)

    session = SessionState.from_metadata(
        CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
    )
    gemini = FakeGeminiClient(
        [
            GeminiLiveEvent(audio=(100).to_bytes(2, "little", signed=True) * 240),
            GeminiLiveEvent(interrupted=True),
        ]
    )
    bridge = AudioBridge(session, settings, gemini, send_message)

    await bridge.start(_build_start_event())
    await asyncio.sleep(0)

    sent_events = [message.event for message in sent_messages]
    assert "media" in sent_events
    assert "mark" in sent_events
    assert "clear" in sent_events

    await bridge.close()


@pytest.mark.asyncio
async def test_audio_bridge_releases_suppression_when_twilio_acknowledges_playback(settings) -> None:
    sent_messages = []

    async def send_message(message) -> None:
        sent_messages.append(message)

    session = SessionState.from_metadata(
        CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
    )
    gemini = FakeGeminiClient([GeminiLiveEvent(audio=(100).to_bytes(2, "little", signed=True) * 240)])
    bridge = AudioBridge(session, settings, gemini, send_message)

    await bridge.start(_build_start_event())
    await asyncio.sleep(0)

    mark_name = next(message.mark.name for message in sent_messages if message.event == "mark")
    assert bridge._suppress_input_until > monotonic()

    await bridge.handle_twilio_mark(_build_mark_event(mark_name))

    assert bridge._suppress_input_until == 0.0

    await bridge.close()


@pytest.mark.asyncio
async def test_audio_bridge_detects_caller_barge_in_during_playback(settings) -> None:
    sent_messages = []

    async def send_message(message) -> None:
        sent_messages.append(message)

    session = SessionState.from_metadata(
        CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
    )
    gemini = FakeGeminiClient([GeminiLiveEvent(audio=(100).to_bytes(2, "little", signed=True) * 240)])
    bridge = AudioBridge(session, settings, gemini, send_message)

    await bridge.start(_build_start_event())
    await asyncio.sleep(0)

    loud_pcm = (12000).to_bytes(2, "little", signed=True) * 160
    loud_ulaw = pcm16le_to_ulaw_bytes(loud_pcm)
    barge_in_event = TwilioMediaEvent.model_validate(
        {
            "event": "media",
            "streamSid": "MZ123",
            "media": {"payload": encode_base64_payload(loud_ulaw)},
        }
    )

    await bridge.handle_twilio_media(barge_in_event)
    await bridge.handle_twilio_media(barge_in_event)
    await bridge.handle_twilio_media(barge_in_event)

    sent_events = [message.event for message in sent_messages]
    assert "clear" in sent_events
    assert gemini.sent_audio

    await bridge.close()


@pytest.mark.asyncio
async def test_audio_bridge_close_is_idempotent(settings) -> None:
    sent_messages = []

    async def send_message(message) -> None:
        sent_messages.append(message)

    session = SessionState.from_metadata(
        CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
    )
    gemini = FakeGeminiClient([])
    bridge = AudioBridge(session, settings, gemini, send_message)

    await bridge.start(_build_start_event())
    await bridge.close()
    await bridge.close()

    assert gemini.disconnected is True


@pytest.mark.asyncio
async def test_audio_bridge_stitches_delta_agent_transcripts_into_one_turn(settings) -> None:
    sent_messages = []

    async def send_message(message) -> None:
        sent_messages.append(message)

    session = SessionState.from_metadata(
        CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
    )
    gemini = FakeGeminiClient(
        [
            GeminiLiveEvent(transcript="Hello"),
            GeminiLiveEvent(transcript="how can"),
            GeminiLiveEvent(transcript="I help you today?", turn_complete=True),
        ]
    )
    bridge = AudioBridge(session, settings, gemini, send_message)

    await bridge.start(_build_start_event())
    await asyncio.sleep(0)

    assert gemini.agent_turns == ["Hello how can I help you today?"]

    await bridge.close()


def test_merge_transcript_handles_mixed_partial_shapes() -> None:
    assert _merge_transcript("", "Hello") == "Hello"
    assert _merge_transcript("Hello", "Hello there") == "Hello there"
    assert _merge_transcript("Hello there", "there") == "Hello there"
    assert _merge_transcript("Hello how can I", "can I help") == "Hello how can I help"
    assert _merge_transcript("Hello", "world") == "Hello world"


@pytest.mark.asyncio
async def test_audio_bridge_notifies_testing_progress_on_committed_turns(settings) -> None:
    sent_messages = []

    async def send_message(message) -> None:
        sent_messages.append(message)

    session = SessionState.from_metadata(
        CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
    )
    gemini = FakeGeminiClient(
        [
            GeminiLiveEvent(input_transcript="Caller question"),
            GeminiLiveEvent(transcript="Patient response", turn_complete=True),
        ]
    )
    manager = FakeTestCampaignManager()
    bridge = AudioBridge(session, settings, gemini, send_message, test_campaign_manager=manager)

    await bridge.start(_build_start_event())
    await asyncio.sleep(0)

    assert manager.updated_sessions == ["CA123"]

    await bridge.close()


@pytest.mark.asyncio
async def test_audio_bridge_waits_before_sending_transition_nudge(settings) -> None:
    settings.representative_turn_settle_seconds = 0.05
    sent_messages = []

    async def send_message(message) -> None:
        sent_messages.append(message)

    session = SessionState.from_metadata(
        CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
    )
    gemini = FakeGeminiClient(
        [
            GeminiLiveEvent(input_transcript="Caller question"),
            GeminiLiveEvent(transcript="Patient response", turn_complete=True),
        ]
    )
    manager = FakeTestCampaignManager()
    manager.transition_nudges.append("move to next scenario")
    bridge = AudioBridge(session, settings, gemini, send_message, test_campaign_manager=manager)

    await bridge.start(_build_start_event())
    await asyncio.sleep(0.01)

    assert gemini.sent_text_instructions == []

    await asyncio.sleep(0.08)

    assert gemini.sent_text_instructions == ["move to next scenario"]
    assert gemini.call_instructions[-1] == "instruction for CA123"

    await bridge.close()


@pytest.mark.asyncio
async def test_audio_bridge_extends_transition_wait_on_new_representative_activity(settings) -> None:
    settings.representative_turn_settle_seconds = 0.05
    settings.representative_activity_amplitude_threshold = 50
    sent_messages = []

    async def send_message(message) -> None:
        sent_messages.append(message)

    session = SessionState.from_metadata(
        CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
    )
    gemini = FakeGeminiClient(
        [
            GeminiLiveEvent(input_transcript="Caller question"),
            GeminiLiveEvent(transcript="Patient response", turn_complete=True),
        ]
    )
    manager = FakeTestCampaignManager()
    manager.transition_nudges.append("move to next scenario")
    bridge = AudioBridge(session, settings, gemini, send_message, test_campaign_manager=manager)

    await bridge.start(_build_start_event())
    await asyncio.sleep(0.02)

    # Fresh inbound audio activity should extend the settle window.
    await bridge.handle_twilio_media(_build_media_event())
    await asyncio.sleep(0.03)
    assert gemini.sent_text_instructions == []

    await asyncio.sleep(0.06)
    assert gemini.sent_text_instructions == ["move to next scenario"]

    await bridge.close()


@pytest.mark.asyncio
async def test_audio_bridge_sends_wait_instruction_when_support_is_checking(settings) -> None:
    sent_messages = []

    async def send_message(message) -> None:
        sent_messages.append(message)

    session = SessionState.from_metadata(
        CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
    )
    gemini = FakeGeminiClient(
        [
            GeminiLiveEvent(input_transcript="Let me check that for you"),
            GeminiLiveEvent(input_transcript="Let me check that for you please"),
        ]
    )
    bridge = AudioBridge(session, settings, gemini, send_message)

    await bridge.start(_build_start_event())
    await asyncio.sleep(0)

    assert len(gemini.sent_text_instructions) == 1
    assert "The representative is still checking the current issue" in gemini.sent_text_instructions[0]

    await bridge.close()
