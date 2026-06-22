from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from voicebot.src.api.server import create_app
from voicebot.src.models.api_models import RecordingSummary
from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.models.session_state import SessionState


class FakeCallManager:
    def __init__(self) -> None:
        self.requests = []
        self.hangups = []
        self.session = SessionState.from_metadata(
            CallMetadata(
                call_sid="CA123",
                to_number="+15555550124",
                from_number="+15555550123",
                stream_url="wss://example.test/ws/twilio-media",
                status="queued",
            )
        )
        self.active_calls = {"CA123": self.session}

    def create_outbound_call(self, **kwargs):
        self.requests.append(kwargs)
        return CallMetadata(
            call_sid="CA123",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
            status="queued",
        )

    def list_calls(self, active_only: bool = False):
        return [self.session]

    def hang_up_call(self, call_sid: str):
        self.hangups.append(call_sid)
        self.session.mark_completed()
        self.active_calls.pop(call_sid, None)
        return self.session


class FakeRecordingManager:
    def __init__(self) -> None:
        self.recording_path = Path("/tmp/test-call.mp3")

    def list_recordings(self):
        return [
            RecordingSummary(
                call_sid="CA123",
                recording_sid="RE123",
                saved_at="2026-06-19T00:00:00Z",
                filename=self.recording_path.name,
                display_name="test-call",
                deep_analysis_filename="test-call.deep-analysis.md",
                recording_path=str(self.recording_path),
            )
        ]

    def resolve_recording_path(self, filename: str) -> Path:
        return self.recording_path

    def resolve_deep_analysis_path(self, filename: str) -> Path:
        return self.recording_path.with_name("test-call.deep-analysis.md")

    async def close(self) -> None:
        return None


def test_server_healthz(settings) -> None:
    app = create_app(settings=settings, call_manager=FakeCallManager(), recording_manager=FakeRecordingManager())
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_server_outbound_call_route(settings) -> None:
    manager = FakeCallManager()
    app = create_app(settings=settings, call_manager=manager, recording_manager=FakeRecordingManager())
    client = TestClient(app)

    response = client.post("/calls/outbound", json={"record_call": False})

    assert response.status_code == 200
    assert response.json()["call_sid"] == "CA123"
    assert manager.requests[0]["record_call"] is False


def test_dashboard_page_is_served(settings) -> None:
    app = create_app(settings=settings, call_manager=FakeCallManager(), recording_manager=FakeRecordingManager())
    client = TestClient(app)

    landing_response = client.get("/")
    dashboard_response = client.get("/dashboard")

    assert landing_response.status_code == 200
    assert "PatientZero" in landing_response.text
    assert dashboard_response.status_code == 200
    assert "Voicebot Control Room" in dashboard_response.text
    assert 'class="signal"' not in dashboard_response.text
    assert "Reset Scenario Progress" in dashboard_response.text


def test_dashboard_assets_are_served(settings) -> None:
    app = create_app(settings=settings, call_manager=FakeCallManager(), recording_manager=FakeRecordingManager())
    client = TestClient(app)

    css_response = client.get("/assets/dashboard.css")
    js_response = client.get("/assets/dashboard.js")

    assert css_response.status_code == 200
    assert ".chat-bubble.live::before" in css_response.text
    assert js_response.status_code == 200
    assert "scheduleRecordingsRefresh" in js_response.text


def test_dashboard_snapshot_returns_calls(settings) -> None:
    app = create_app(settings=settings, call_manager=FakeCallManager(), recording_manager=FakeRecordingManager())
    client = TestClient(app)

    client.get("/healthz")
    response = client.get("/api/dashboard/snapshot")

    assert response.status_code == 200
    assert response.json()["recent_calls"][0]["call_sid"] == "CA123"
    assert "transcript_messages" in response.json()
    assert "recordings" in response.json()


def test_dashboard_hangup_route(settings) -> None:
    manager = FakeCallManager()
    app = create_app(settings=settings, call_manager=manager, recording_manager=FakeRecordingManager())
    client = TestClient(app)

    response = client.post("/api/calls/CA123/hangup")

    assert response.status_code == 200
    assert response.json()["call_sid"] == "CA123"
    assert manager.hangups == ["CA123"]


def test_recordings_api_returns_saved_recordings(settings) -> None:
    app = create_app(settings=settings, call_manager=FakeCallManager(), recording_manager=FakeRecordingManager())
    client = TestClient(app)

    response = client.get("/api/recordings")

    assert response.status_code == 200
    assert response.json()[0]["recording_sid"] == "RE123"
