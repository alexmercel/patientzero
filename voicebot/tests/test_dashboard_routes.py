from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from voicebot.src.api.dashboard_routes import build_dashboard_router
from voicebot.src.models.api_models import RecordingSummary
from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.models.session_state import SessionState
from voicebot.src.observability.event_bus import DashboardEventBus
from voicebot.src.testing.campaign_manager import TestCampaignManager


class FakeCallManager:
    def __init__(self) -> None:
        self.session = SessionState.from_metadata(
            CallMetadata(
                call_sid="CA123",
                to_number="+15555550124",
                from_number="+15555550123",
                stream_url="wss://example.test/ws/twilio-media",
            )
        )

    def list_calls(self, active_only: bool = False):
        return [self.session]

    @property
    def active_calls(self):
        return {"CA123": self.session}

    def create_outbound_call(self, **kwargs):
        return CallMetadata(
            call_sid="CA999",
            to_number="+15555550124",
            from_number="+15555550123",
            stream_url="wss://example.test/ws/twilio-media",
            custom_parameters=kwargs.get("custom_parameters") or {},
        )


class FakeRecordingManager:
    def __init__(self) -> None:
        self.favorite = False

    def list_recordings(self):
        return [
            RecordingSummary(
                call_sid="CA123",
                recording_sid="RE123",
                saved_at="2026-06-19T00:00:00Z",
                filename="call.mp3",
                display_name="call",
                report_filename="call.md",
                report_available=True,
                deep_analysis_filename="call.deep-analysis.md",
                deep_analysis_available=True,
                favorite=self.favorite,
                recording_path="/tmp/call.mp3",
            )
        ]

    def update_favorite(self, filename: str, favorite: bool | None = None):
        self.favorite = (not self.favorite) if favorite is None else bool(favorite)
        return self.list_recordings()[0]

    def resolve_report_path(self, filename: str):
        from pathlib import Path

        path = Path("/tmp/call.md")
        path.write_text("# report\n", encoding="utf-8")
        return path

    def resolve_deep_analysis_path(self, filename: str):
        from pathlib import Path

        path = Path("/tmp/call.deep-analysis.md")
        path.write_text("# deep analysis\n", encoding="utf-8")
        return path


def test_dashboard_websocket_streams_events(settings) -> None:
    event_bus = DashboardEventBus()
    app = FastAPI()
    app.include_router(
        build_dashboard_router(
            settings,
            FakeCallManager(),
            event_bus,
            FakeRecordingManager(),
            TestCampaignManager(settings),
        )
    )
    client = TestClient(app)

    with client.websocket_connect("/ws/dashboard") as websocket:
        snapshot = websocket.receive_json()
        assert snapshot["type"] == "snapshot"

        event_bus.publish(
            "log",
            {
                "event": "call_transcript_updated",
                "call_sid": "CA123",
                "speaker": "agent",
                "text": "Hello there",
                "level": "info",
            },
        )
        transcript_event = websocket.receive_json()
        assert transcript_event["type"] == "event"
        assert transcript_event["event"]["payload"]["transcript_message"]["text"] == "Hello there"

        event_bus.publish("log", {"event": "hello_world", "level": "info"})
        message = websocket.receive_json()
        assert message["type"] == "event"
        assert message["event"]["payload"]["event"] == "hello_world"


def test_testing_report_and_start_call_routes(settings) -> None:
    event_bus = DashboardEventBus()
    app = FastAPI()
    manager = TestCampaignManager(settings)
    app.include_router(
        build_dashboard_router(
            settings,
            FakeCallManager(),
            event_bus,
            FakeRecordingManager(),
            manager,
        )
    )
    client = TestClient(app)

    report_response = client.get("/api/testing/report")
    next_call_response = client.get("/api/testing/next-call")
    start_response = client.post("/api/testing/start-call")

    assert report_response.status_code == 200
    assert report_response.json()["summary"]["total_scenarios"] >= 1
    assert report_response.json()["next_run_preview"]["run_id"]
    assert next_call_response.status_code == 200
    assert next_call_response.json()["run_id"]
    assert start_response.status_code == 200
    assert start_response.json()["run_id"]
    assert start_response.json()["current_scenario"]["scenario_id"]
    assert start_response.json()["run_id"] == next_call_response.json()["run_id"]


def test_testing_reset_route_clears_campaign_summary(settings) -> None:
    event_bus = DashboardEventBus()
    app = FastAPI()
    manager = TestCampaignManager(settings)
    run, custom_parameters = manager.plan_next_call()
    metadata = CallMetadata(
        call_sid="CA_RESET",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    manager.activate_run(metadata)
    session = SessionState.from_metadata(metadata)
    scenario = manager._resolved_scenarios_for_run(manager._active_runs["CA_RESET"])[0]
    session.append_transcript_turn("agent", scenario.ask[0])
    session.append_transcript_turn("user", "I can help with that.")
    manager.finalize_call(session)
    app.include_router(
        build_dashboard_router(
            settings,
            FakeCallManager(),
            event_bus,
            FakeRecordingManager(),
            manager,
        )
    )
    client = TestClient(app)

    response = client.post("/api/testing/reset")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["testing"]["summary"]["tested_count"] == 0
    assert response.json()["testing"]["recent_runs"]


def test_recording_report_route_returns_markdown(settings) -> None:
    event_bus = DashboardEventBus()
    app = FastAPI()
    app.include_router(
        build_dashboard_router(
            settings,
            FakeCallManager(),
            event_bus,
            FakeRecordingManager(),
            TestCampaignManager(settings),
        )
    )
    client = TestClient(app)

    response = client.get("/api/recordings/call.mp3/report")

    assert response.status_code == 200
    assert "# report" in response.text


def test_recording_deep_analysis_route_returns_markdown(settings) -> None:
    event_bus = DashboardEventBus()
    app = FastAPI()
    app.include_router(
        build_dashboard_router(
            settings,
            FakeCallManager(),
            event_bus,
            FakeRecordingManager(),
            TestCampaignManager(settings),
        )
    )
    client = TestClient(app)

    response = client.get("/api/recordings/call.mp3/deep-analysis")

    assert response.status_code == 200
    assert "# deep analysis" in response.text


def test_recording_favorite_route_updates_recording(settings) -> None:
    event_bus = DashboardEventBus()
    app = FastAPI()
    recordings = FakeRecordingManager()
    app.include_router(
        build_dashboard_router(
            settings,
            FakeCallManager(),
            event_bus,
            recordings,
            TestCampaignManager(settings),
        )
    )
    client = TestClient(app)

    response = client.post("/api/recordings/call.mp3/favorite", json={"favorite": True})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["recording"]["favorite"] is True
