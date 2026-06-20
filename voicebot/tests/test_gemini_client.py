from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from voicebot.src.realtime.gemini_live_client import GeminiLiveClient


class FakeSession:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent_audio = []

    async def send_realtime_input(self, audio):
        self.sent_audio.append(audio)

    async def receive(self):
        for message in self.messages:
            if isinstance(message, Exception):
                raise message
            yield message


class FakeContextManager:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.closed = False

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True


class FakeLiveConnector:
    def __init__(self, sessions):
        self.sessions = list(sessions)
        self.connect_kwargs = []

    def connect(self, **kwargs):
        self.connect_kwargs.append(kwargs)
        session = self.sessions.pop(0)
        return FakeContextManager(session)


class FakeGenAiClient:
    def __init__(self, sessions):
        self.live_connector = FakeLiveConnector(sessions)
        self.aio = SimpleNamespace(live=self.live_connector)


def _message_with_audio(audio: bytes, transcript: str = "hello"):
    inline = SimpleNamespace(mime_type="audio/pcm;rate=24000", data=audio)
    part = SimpleNamespace(inline_data=inline)
    model_turn = SimpleNamespace(parts=[part])
    server_content = SimpleNamespace(
        model_turn=model_turn,
        output_transcription=SimpleNamespace(text=transcript),
        input_transcription=SimpleNamespace(text="user said hi"),
        turn_complete=True,
    )
    return SimpleNamespace(server_content=server_content)


def _message_with_resumption_handle(handle: str):
    return SimpleNamespace(
        server_content=SimpleNamespace(model_turn=SimpleNamespace(parts=[]), turn_complete=False),
        session_resumption_update=SimpleNamespace(resumable=True, new_handle=handle),
    )


def _message_with_input_only(text: str):
    return SimpleNamespace(
        server_content=SimpleNamespace(
            model_turn=SimpleNamespace(parts=[]),
            output_transcription=None,
            input_transcription=SimpleNamespace(text=text),
            turn_complete=False,
        )
    )


def _message_with_lifecycle_signals():
    return SimpleNamespace(
        server_content=SimpleNamespace(
            model_turn=SimpleNamespace(parts=[]),
            output_transcription=None,
            input_transcription=None,
            interrupted=True,
            generation_complete=True,
            turn_complete=False,
        ),
        go_away=SimpleNamespace(time_left="5s"),
    )


@pytest.mark.asyncio
async def test_gemini_client_sends_audio(settings) -> None:
    session = FakeSession(messages=[])
    fake_client = FakeGenAiClient([session])
    client = GeminiLiveClient(
        settings,
        client=fake_client,
        blob_factory=lambda **kwargs: kwargs,
    )

    await client.send_audio(b"\x00\x01", sample_rate=8000)

    assert session.sent_audio[0]["mime_type"] == "audio/pcm;rate=8000"
    config = fake_client.live_connector.connect_kwargs[0]["config"]
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == settings.gemini_voice_name
    assert config.system_instruction == settings.gemini_system_instruction


@pytest.mark.asyncio
async def test_gemini_client_reconnects_and_yields_events(settings) -> None:
    first = FakeSession(messages=[_message_with_resumption_handle("handle-1"), RuntimeError("socket closed")])
    second = FakeSession(messages=[_message_with_audio(b"\x01\x02\x03\x04")])
    fake_client = FakeGenAiClient([first, second])
    client = GeminiLiveClient(
        settings,
        client=fake_client,
        blob_factory=lambda **kwargs: kwargs,
    )

    events = client.receive_events()
    event = await anext(events)

    assert event.audio == b"\x01\x02\x03\x04"
    assert event.transcript == "hello"
    assert event.input_transcript == "user said hi"
    reconnect_config = fake_client.live_connector.connect_kwargs[1]["config"]
    assert reconnect_config.session_resumption.handle == "handle-1"

    await client.disconnect()
    await events.aclose()


@pytest.mark.asyncio
async def test_gemini_client_yields_input_only_transcripts(settings) -> None:
    session = FakeSession(messages=[_message_with_input_only("caller said hello")])
    fake_client = FakeGenAiClient([session])
    client = GeminiLiveClient(
        settings,
        client=fake_client,
        blob_factory=lambda **kwargs: kwargs,
    )

    events = client.receive_events()
    event = await anext(events)

    assert event.audio == b""
    assert event.transcript is None
    assert event.input_transcript == "caller said hello"

    await client.disconnect()
    await events.aclose()


@pytest.mark.asyncio
async def test_gemini_client_serializes_concurrent_connects(settings) -> None:
    session = FakeSession(messages=[])
    fake_client = FakeGenAiClient([session])
    client = GeminiLiveClient(
        settings,
        client=fake_client,
        blob_factory=lambda **kwargs: kwargs,
    )

    await asyncio.gather(client.connect(), client.connect())

    assert len(fake_client.live_connector.connect_kwargs) == 1

    await client.disconnect()


@pytest.mark.asyncio
async def test_gemini_client_adds_continuity_note_after_prior_turns(settings) -> None:
    session = FakeSession(messages=[])
    fake_client = FakeGenAiClient([session])
    client = GeminiLiveClient(
        settings,
        client=fake_client,
        blob_factory=lambda **kwargs: kwargs,
    )
    client.remember_user_turn("The company is Athena.")
    client.remember_agent_turn("What are their business hours?")
    client.remember_user_turn("Our hours are 9 to 5.")
    client._session_handle = "handle-1"

    await client.connect()

    config = fake_client.live_connector.connect_kwargs[0]["config"]
    assert "same phone call already in progress" in config.system_instruction
    assert "Stay in character as the patient" in config.system_instruction
    assert "The company is Athena." in config.system_instruction
    assert "What are their business hours?" in config.system_instruction
    assert "Our hours are 9 to 5." in config.system_instruction
    assert "Representative: The company is Athena." in config.system_instruction
    assert "Patient: What are their business hours?" in config.system_instruction
    assert "Facts the representative already provided" in config.system_instruction

    await client.disconnect()


@pytest.mark.asyncio
async def test_gemini_client_yields_lifecycle_signals(settings) -> None:
    session = FakeSession(messages=[_message_with_lifecycle_signals()])
    fake_client = FakeGenAiClient([session])
    client = GeminiLiveClient(
        settings,
        client=fake_client,
        blob_factory=lambda **kwargs: kwargs,
    )

    events = client.receive_events()
    event = await anext(events)

    assert event.interrupted is True
    assert event.generation_complete is True
    assert event.go_away_time_left == "5s"

    await client.disconnect()
    await events.aclose()


@pytest.mark.asyncio
async def test_gemini_client_adds_repeat_answer_guidance(settings) -> None:
    session = FakeSession(messages=[])
    fake_client = FakeGenAiClient([session])
    client = GeminiLiveClient(
        settings,
        client=fake_client,
        blob_factory=lambda **kwargs: kwargs,
    )
    client.remember_user_turn("We are open Monday through Friday from 9 to 5.")
    client.remember_user_turn("We are open Monday through Friday from 9 to 5.")

    await client.connect()

    config = fake_client.live_connector.connect_kwargs[0]["config"]
    assert "repeated the same answer twice" in config.system_instruction
    assert "Do not ask for that same information a third time" in config.system_instruction

    await client.disconnect()
