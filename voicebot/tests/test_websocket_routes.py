from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from voicebot.src.api.websocket_routes import build_router
from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.models.session_state import SessionState
from voicebot.src.realtime.gemini_live_client import GeminiLiveEvent


class FakeCallManager:
    def __init__(self) -> None:
        metadata = CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
        )
        self.session = SessionState.from_metadata(metadata)
        self.status_updates = []
        self.recorded_failures = []

    def get(self, call_sid: str):
        return self.session

    def get_or_create(self, call_sid: str, metadata=None):
        return self.session

    def attach_stream(self, call_sid: str, stream_sid: str):
        self.session.attach_stream(stream_sid)
        self.session.mark_active()
        return self.session

    def update_call_status(self, call_sid: str, status: str):
        self.status_updates.append((call_sid, status))
        return self.session

    def complete_call(self, call_sid: str):
        self.session.mark_completed()
        return self.session

    def record_failure(self, call_sid: str, message: str):
        self.recorded_failures.append((call_sid, message))

    def list_calls(self, active_only: bool = False):
        return [self.session]


class FakeRecordingManager:
    def __init__(self) -> None:
        self.downloads = []

    async def download_and_store(self, **kwargs):
        self.downloads.append(kwargs)

    async def close(self) -> None:
        return None


class FakeGeminiClient:
    def bind_context(self, **context) -> None:
        return None

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def send_audio(self, pcm_bytes: bytes, sample_rate: int) -> None:
        return None

    async def receive_events(self):
        yield GeminiLiveEvent(audio=(100).to_bytes(2, "little", signed=True) * 240)


def _build_app(settings) -> tuple[TestClient, FakeCallManager, FakeRecordingManager]:
    call_manager = FakeCallManager()
    recording_manager = FakeRecordingManager()
    app = FastAPI()
    app.include_router(
        build_router(
            settings=settings,
            call_manager=call_manager,
            recording_manager=recording_manager,
            gemini_client_factory=lambda: FakeGeminiClient(),
        )
    )
    return TestClient(app), call_manager, recording_manager


def test_twilio_status_callback(settings) -> None:
    client, manager, _ = _build_app(settings)
    response = client.post("/twilio/status", data={"CallSid": "CA123", "CallStatus": "completed"})
    assert response.status_code == 200
    assert manager.status_updates == [("CA123", "completed")]


def test_twilio_recording_status_downloads_recording(settings) -> None:
    client, _, recording_manager = _build_app(settings)
    response = client.post(
        "/twilio/recording-status",
        data={
            "CallSid": "CA123",
            "RecordingSid": "RE123",
            "RecordingUrl": "https://api.twilio.test/recording",
            "RecordingStatus": "completed",
        },
    )
    assert response.status_code == 200
    assert recording_manager.downloads[0]["recording_sid"] == "RE123"


def test_twilio_websocket_bridges_audio(settings) -> None:
    client, _, _ = _build_app(settings)
    with client.websocket_connect("/ws/twilio-media") as websocket:
        websocket.send_json(
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
        outbound_messages = [websocket.receive_json(), websocket.receive_json()]
        assert {message["event"] for message in outbound_messages} == {"media", "mark"}
        websocket.send_json(
            {
                "event": "stop",
                "streamSid": "MZ123",
                "stop": {"accountSid": "AC123", "callSid": "CA123"},
            }
        )
