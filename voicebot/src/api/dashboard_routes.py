"""Dashboard UI and live event routes."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from voicebot.src.agents.campaign_agents import CampaignSelectorAgent, ScenarioOrchestratorAgent
from voicebot.src.agents.dashboard_agent import DashboardAgent
from voicebot.src.agents.runtime import AgentRuntime
from voicebot.src.config.settings import Settings
from voicebot.src.models.api_models import (
    RecordingFavoriteRequest,
    ScenarioTransitionRequest,
    StartTestCallRequest,
)
from voicebot.src.observability.event_bus import DashboardEventBus
from voicebot.src.runtime.connection_registry import ConnectionRegistry
from voicebot.src.telephony.call_manager import CallManager
from voicebot.src.telephony.recording_manager import RecordingManager, RecordingManagerError
from voicebot.src.testing.campaign_manager import TestCampaignManager
from voicebot.src.utils.logging import get_logger

logger = get_logger(__name__)
_LANDING_HTML_PATH = Path(__file__).with_name("landing.html")
_DASHBOARD_HTML_PATH = Path(__file__).with_name("dashboard.html")
_DASHBOARD_CSS_PATH = Path(__file__).with_name("dashboard.css")
_DASHBOARD_JS_PATH = Path(__file__).with_name("dashboard.js")


def build_dashboard_router(
    settings: Settings,
    call_manager: CallManager,
    event_bus: DashboardEventBus,
    recording_manager: RecordingManager,
    test_campaign_manager: TestCampaignManager,
    connection_registry: ConnectionRegistry | None = None,
    agent_runtime: AgentRuntime | None = None,
    dashboard_agent: DashboardAgent | None = None,
    campaign_selector_agent: CampaignSelectorAgent | None = None,
    scenario_orchestrator_agent: ScenarioOrchestratorAgent | None = None,
) -> APIRouter:
    """Create routes for the local operator dashboard."""
    router = APIRouter()
    registry = connection_registry or ConnectionRegistry()
    dashboard_snapshots = dashboard_agent or DashboardAgent()
    campaign_selector = campaign_selector_agent or CampaignSelectorAgent(test_campaign_manager)
    scenario_orchestrator = scenario_orchestrator_agent or ScenarioOrchestratorAgent(test_campaign_manager)

    @router.get("/", response_class=HTMLResponse)
    async def landing() -> HTMLResponse:
        return HTMLResponse(_LANDING_HTML_PATH.read_text(encoding="utf-8"))

    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        return HTMLResponse(_DASHBOARD_HTML_PATH.read_text(encoding="utf-8"))

    @router.get("/assets/dashboard.css")
    async def dashboard_css() -> FileResponse:
        return FileResponse(_DASHBOARD_CSS_PATH, media_type="text/css")

    @router.get("/assets/dashboard.js")
    async def dashboard_js() -> FileResponse:
        return FileResponse(_DASHBOARD_JS_PATH, media_type="application/javascript")

    @router.get("/api/dashboard/snapshot")
    async def dashboard_snapshot() -> JSONResponse:
        return JSONResponse(
            dashboard_snapshots.snapshot(
                settings=settings,
                call_manager=call_manager,
                event_bus=event_bus,
                recording_manager=recording_manager,
                test_campaign_manager=test_campaign_manager,
            )
        )

    @router.get("/api/agents")
    async def agent_status() -> JSONResponse:
        if agent_runtime is None:
            return JSONResponse({"started": False, "agent_count": 0, "agents": []})
        return JSONResponse(agent_runtime.status())

    @router.get("/api/testing/report")
    async def testing_report() -> JSONResponse:
        recordings = [recording.model_dump(mode="json") for recording in recording_manager.list_recordings()]
        return JSONResponse(test_campaign_manager.report_snapshot(recordings=recordings))

    @router.get("/api/testing/next-call")
    async def testing_next_call() -> JSONResponse:
        return JSONResponse(campaign_selector.preview_next_call().model_dump(mode="json"))

    @router.post("/api/testing/reset")
    async def reset_testing_report() -> JSONResponse:
        campaign_selector.reset_campaign()
        recordings = [recording.model_dump(mode="json") for recording in recording_manager.list_recordings()]
        return JSONResponse(
            {
                "ok": True,
                "testing": test_campaign_manager.report_snapshot(recordings=recordings),
            }
        )

    @router.get("/api/testing/personas")
    async def list_testing_personas() -> JSONResponse:
        return JSONResponse(campaign_selector.list_personas())

    @router.get("/api/testing/scenarios")
    async def list_testing_scenarios() -> JSONResponse:
        return JSONResponse(campaign_selector.list_scenarios())

    @router.post("/api/testing/start-call")
    async def start_testing_call(
        request: StartTestCallRequest | None = None,
    ) -> JSONResponse:
        persona_id = request.persona_id if request else None
        scenario_ids = request.scenario_ids if request else None
        run, custom_parameters = campaign_selector.plan_next_call(
            persona_id=persona_id,
            scenario_ids=scenario_ids,
        )
        metadata = call_manager.create_outbound_call(custom_parameters=custom_parameters)
        activated_run = test_campaign_manager.activate_run(metadata)
        event_bus.begin_call(metadata.call_sid)
        return JSONResponse(
            {
                "call_sid": metadata.call_sid,
                "status": metadata.status,
                "run_id": activated_run.run_id if activated_run is not None else run.run_id,
                "persona_id": run.persona_id,
                "persona_name": run.persona_name,
                "scenario_ids": run.scenario_ids,
                "current_scenario": (
                    activated_run.current_scenario.model_dump(mode="json")
                    if activated_run is not None and activated_run.current_scenario is not None
                    else (
                        run.current_scenario.model_dump(mode="json")
                        if run.current_scenario is not None
                        else None
                    )
                ),
            }
        )

    @router.post("/api/testing/active-runs/{call_sid}/scenario-transition")
    async def transition_active_scenario(
        call_sid: str,
        request: ScenarioTransitionRequest,
    ) -> JSONResponse:
        try:
            transition = scenario_orchestrator.transition_scenario(
                call_sid=call_sid,
                mode=request.mode,
                target_scenario_id=request.target_scenario_id,
                reason=request.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        nudge = transition.get("transition_nudge")
        bridge = await registry.bridge_for_call(call_sid)
        transition_queued = False
        if bridge is not None and nudge and hasattr(bridge, "queue_transition_nudge"):
            transition_queued = bool(bridge.queue_transition_nudge(nudge))

        event_bus.publish(
            "state",
            {
                "event": "testing_scenario_transition_queued",
                "call_sid": call_sid,
                "run_id": transition.get("run", {}).get("run_id"),
                "mode": transition.get("mode"),
                "target_scenario_id": transition.get("target_scenario_id"),
                "transition_queued": transition_queued,
            },
        )
        return JSONResponse(
            {
                "ok": True,
                "call_sid": call_sid,
                "run_id": transition.get("run", {}).get("run_id"),
                "current_scenario": transition.get("current_scenario"),
                "transition_status": "queued" if transition_queued else "state_updated",
                "transition_queued": transition_queued,
                "transition_nudge": nudge,
                "mode": transition.get("mode"),
                "target_scenario_id": transition.get("target_scenario_id"),
            }
        )

    @router.post("/api/calls/{call_sid}/hangup")
    async def hangup_call(call_sid: str) -> JSONResponse:
        try:
            session = call_manager.hang_up_call(call_sid)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse({"call_sid": session.call_sid, "status": session.status.value})

    @router.get("/api/recordings")
    async def list_recordings() -> JSONResponse:
        payload = [recording.model_dump(mode="json") for recording in recording_manager.list_recordings()]
        return JSONResponse(payload)

    @router.post("/api/recordings/{filename}/favorite")
    async def favorite_recording(filename: str, request: RecordingFavoriteRequest) -> JSONResponse:
        try:
            recording = recording_manager.update_favorite(filename, request.favorite)
        except RecordingManagerError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        event_bus.publish(
            "log",
            {
                "event": "recording_favorite_updated",
                "filename": recording.filename,
                "favorite": recording.favorite,
                "level": "info",
            },
        )
        return JSONResponse({"ok": True, "recording": recording.model_dump(mode="json")})

    @router.get("/api/recordings/{filename}")
    async def get_recording(filename: str) -> FileResponse:
        try:
            path = recording_manager.resolve_recording_path(filename)
        except RecordingManagerError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type="audio/mpeg", filename=path.name)

    @router.get("/api/recordings/{filename}/report")
    async def get_recording_report(filename: str) -> FileResponse:
        try:
            path = recording_manager.resolve_report_path(filename)
        except RecordingManagerError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type="text/markdown", filename=path.name)

    @router.get("/api/recordings/{filename}/deep-analysis")
    async def get_recording_deep_analysis(filename: str) -> FileResponse:
        try:
            path = recording_manager.resolve_deep_analysis_path(filename)
        except RecordingManagerError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type="text/markdown", filename=path.name)

    @router.websocket("/ws/dashboard")
    async def dashboard_events(websocket: WebSocket) -> None:
        await websocket.accept()
        await registry.register_websocket(websocket)
        subscriber_id, queue = event_bus.subscribe()
        logger.info("dashboard_websocket_connected", client=str(websocket.client))
        try:
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "events": event_bus.recent(limit=200),
                    "transcript_messages": event_bus.transcript_messages(),
                }
            )
            while not registry.shutdown_event.is_set():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    continue
                await websocket.send_json({"type": "event", "event": event})
        except WebSocketDisconnect:
            logger.info("dashboard_websocket_disconnected", client=str(websocket.client))
        except asyncio.CancelledError:
            logger.info("dashboard_websocket_cancelled", client=str(websocket.client))
            raise
        finally:
            event_bus.unsubscribe(subscriber_id)
            await registry.unregister_websocket(websocket)

    return router


def _safe_url(settings: Settings) -> str | None:
    try:
        return settings.build_stream_url()
    except ValueError:
        return None
