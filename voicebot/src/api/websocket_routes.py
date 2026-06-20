"""FastAPI routes for Twilio callbacks and media streams."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from voicebot.src.config.settings import Settings
from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.models.session_state import SessionState
from voicebot.src.models.twilio_stream import (
    TwilioMarkEvent,
    TwilioMediaEvent,
    TwilioStartEvent,
    TwilioStopEvent,
)
from voicebot.src.observability.event_bus import DashboardEventBus
from voicebot.src.realtime.audio_bridge import AudioBridge
from voicebot.src.realtime.gemini_live_client import GeminiLiveClient
from voicebot.src.runtime.connection_registry import ConnectionRegistry
from voicebot.src.telephony.call_manager import CallManager
from voicebot.src.telephony.recording_manager import RecordingManager
from voicebot.src.testing.campaign_manager import TestCampaignManager
from voicebot.src.utils.logging import get_logger


def build_router(
    settings: Settings,
    call_manager: CallManager,
    recording_manager: RecordingManager,
    event_bus: DashboardEventBus | None = None,
    test_campaign_manager: TestCampaignManager | None = None,
    gemini_client_factory: Callable[[], GeminiLiveClient] | None = None,
    connection_registry: ConnectionRegistry | None = None,
) -> APIRouter:
    """Create Twilio-facing HTTP and WebSocket routes."""
    router = APIRouter()
    logger = get_logger(__name__)
    gemini_factory = gemini_client_factory or (lambda: GeminiLiveClient(settings))
    registry = connection_registry or ConnectionRegistry()

    async def parse_twilio_form(request: Request) -> dict[str, str]:
        """Parse standard Twilio webhook bodies without multipart helpers."""
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    @router.post("/twilio/status")
    async def twilio_status(request: Request) -> JSONResponse:
        form = await parse_twilio_form(request)
        call_sid = str(form.get("CallSid", "")).strip()
        call_status = str(form.get("CallStatus", "")).strip()
        if not call_sid or not call_status:
            raise HTTPException(status_code=400, detail="Missing CallSid or CallStatus.")
        call_manager.update_call_status(call_sid, call_status)
        if event_bus is not None and call_status in {"completed", "failed", "busy", "no-answer", "canceled"}:
            event_bus.end_call(call_sid)
        if test_campaign_manager is not None and call_status in {"failed", "busy", "no-answer", "canceled"}:
            session = call_manager.get(call_sid)
            if session is not None:
                test_campaign_manager.finalize_call(session)
        logger.info("twilio_status_callback_received", call_sid=call_sid, status=call_status)
        return JSONResponse({"ok": True})

    @router.post("/twilio/recording-status")
    async def recording_status(request: Request) -> JSONResponse:
        form = await parse_twilio_form(request)
        call_sid = str(form.get("CallSid", "")).strip()
        recording_sid = str(form.get("RecordingSid", "")).strip()
        recording_url = str(form.get("RecordingUrl", "")).strip()
        recording_status = str(form.get("RecordingStatus", "")).strip()
        if not call_sid or not recording_sid or not recording_status:
            raise HTTPException(status_code=400, detail="Missing recording callback fields.")

        logger.info(
            "twilio_recording_status_received",
            call_sid=call_sid,
            recording_sid=recording_sid,
            recording_status=recording_status,
        )

        if recording_status == "completed" and recording_url:
            session = call_manager.get(call_sid)
            metadata = session.metadata if session is not None else None
            await recording_manager.download_and_store(
                call_sid=call_sid,
                recording_sid=recording_sid,
                media_url=f"{recording_url}.mp3",
                metadata=metadata,
            )
            if test_campaign_manager is not None:
                test_campaign_manager.sync_recordings(recording_manager.list_recordings())

        return JSONResponse({"ok": True})

    @router.post("/twilio/stream-status")
    async def stream_status(request: Request) -> JSONResponse:
        form = await parse_twilio_form(request)
        logger.info(
            "twilio_stream_status_received",
            call_sid=str(form.get("CallSid", "")).strip() or None,
            stream_sid=str(form.get("StreamSid", "")).strip() or None,
            stream_event=str(form.get("StreamEvent", "")).strip() or None,
            stream_error=str(form.get("StreamError", "")).strip() or None,
            payload=form,
        )
        return JSONResponse({"ok": True})

    @router.websocket(settings.twilio_websocket_path)
    async def twilio_media_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        await registry.register_websocket(websocket)
        logger.info("twilio_websocket_connected", client=str(websocket.client))

        bridge: AudioBridge | None = None
        session_state: SessionState | None = None

        async def send_twilio_message(message: Any) -> None:
            await websocket.send_json(message.model_dump(by_alias=True))

        try:
            while True:
                raw_message = await websocket.receive_text()
                payload = json.loads(raw_message)
                event_type = payload.get("event")

                if event_type == "start":
                    event = TwilioStartEvent.model_validate(payload)
                    if event_bus is not None:
                        event_bus.begin_call(event.start.call_sid)
                    session_state = call_manager.get_or_create(
                        event.start.call_sid,
                        metadata=CallMetadata(
                            call_sid=event.start.call_sid,
                            to_number="unknown",
                            from_number="unknown",
                            stream_url="wss://unknown.invalid/ws/twilio-media",
                            recording_enabled=True,
                            custom_parameters=event.start.custom_parameters,
                        ),
                    )
                    call_manager.attach_stream(event.start.call_sid, event.start.stream_sid)
                    bridge = AudioBridge(
                        session_state=session_state,
                        settings=settings,
                        gemini_client=gemini_factory(),
                        send_twilio_message=send_twilio_message,
                        test_campaign_manager=test_campaign_manager,
                    )
                    if test_campaign_manager is not None:
                        bridge.gemini_client.set_call_instruction(
                            test_campaign_manager.build_call_instruction(event.start.call_sid)
                        )
                    await registry.register_bridge(bridge)
                    await bridge.start(event)
                    continue

                if event_type == "media" and bridge is not None:
                    try:
                        await bridge.handle_twilio_media(TwilioMediaEvent.model_validate(payload))
                    except Exception as exc:
                        logger.exception(
                            "twilio_media_forward_failed",
                            call_sid=getattr(session_state, "call_sid", None),
                            stream_sid=getattr(session_state, "stream_sid", None),
                        )
                        if session_state and session_state.call_sid:
                            call_manager.record_failure(session_state.call_sid, str(exc))
                    continue

                if event_type == "mark":
                    event = TwilioMarkEvent.model_validate(payload)
                    if bridge is not None:
                        await bridge.handle_twilio_mark(event)
                    logger.info(
                        "twilio_websocket_mark_received",
                        call_sid=getattr(session_state, "call_sid", None),
                        stream_sid=event.stream_sid,
                        mark_name=event.mark.name,
                    )
                    continue

                if event_type == "connected":
                    logger.info("twilio_websocket_connected_event", payload=payload)
                    continue

                if event_type == "stop":
                    event = TwilioStopEvent.model_validate(payload)
                    if bridge is not None:
                        await bridge.close()
                    completed_session = call_manager.complete_call(event.stop.call_sid)
                    if event_bus is not None:
                        event_bus.end_call(event.stop.call_sid)
                    if test_campaign_manager is not None:
                        test_campaign_manager.finalize_call(completed_session)
                    logger.info("twilio_websocket_stop_received", call_sid=event.stop.call_sid)
                    return

                logger.warning("twilio_websocket_unhandled_event", event_type=event_type)
        except WebSocketDisconnect:
            logger.info("twilio_websocket_disconnected", call_sid=getattr(session_state, "call_sid", None))
        except Exception as exc:
            logger.exception("twilio_websocket_failed")
            if session_state and session_state.call_sid:
                call_manager.record_failure(session_state.call_sid, str(exc))
                if event_bus is not None:
                    event_bus.end_call(session_state.call_sid)
        finally:
            if bridge is not None:
                await registry.unregister_bridge(bridge)
                await bridge.close()
            if test_campaign_manager is not None and session_state is not None:
                test_campaign_manager.finalize_call(session_state)
            await registry.unregister_websocket(websocket)

    return router
