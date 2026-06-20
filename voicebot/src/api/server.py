"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from voicebot.src.api.dashboard_routes import build_dashboard_router
from voicebot.src.api.websocket_routes import build_router
from voicebot.src.config.settings import Settings, get_settings
from voicebot.src.models.api_models import HealthResponse, OutboundCallRequest, OutboundCallResponse
from voicebot.src.observability.event_bus import DashboardEventBus
from voicebot.src.runtime.connection_registry import ConnectionRegistry
from voicebot.src.telephony.call_manager import CallManager
from voicebot.src.telephony.recording_manager import RecordingManager
from voicebot.src.testing.campaign_manager import TestCampaignManager
from voicebot.src.testing.deep_analysis_service import DeepAnalysisService, DeepAnalysisServiceError
from voicebot.src.telephony.twilio_client import TwilioClient, TwilioClientError
from voicebot.src.utils.logging import configure_logging, get_logger


def create_app(
    settings: Settings | None = None,
    twilio_client: TwilioClient | None = None,
    call_manager: CallManager | None = None,
    recording_manager: RecordingManager | None = None,
    deep_analysis_service: DeepAnalysisService | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    app_settings = settings or get_settings()
    event_bus = DashboardEventBus()
    connection_registry = ConnectionRegistry()
    app_settings.ensure_directories()
    configure_logging(
        app_settings.log_level,
        event_publisher=event_bus.publish,
        log_file_path=app_settings.logs_dir / "app.log",
    )
    logger = get_logger(__name__)
    if deep_analysis_service is not None:
        app_deep_analysis_service = deep_analysis_service
    else:
        try:
            app_deep_analysis_service = DeepAnalysisService(app_settings)
        except DeepAnalysisServiceError:
            logger.exception("deep_analysis_service_init_failed")
            app_deep_analysis_service = None
    test_campaign_manager = TestCampaignManager(
        app_settings,
        event_bus=event_bus,
        deep_analysis_service=app_deep_analysis_service,
    )

    if call_manager is None:
        app_twilio_client = twilio_client or TwilioClient(app_settings)
        app_call_manager = CallManager(app_settings, app_twilio_client)
    else:
        app_call_manager = call_manager
    app_recording_manager = recording_manager or RecordingManager(app_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logger.info("server_starting", host=app_settings.app_host, port=app_settings.app_port)
        try:
            yield
        finally:
            await connection_registry.close_all()
            if app_deep_analysis_service is not None:
                await app_deep_analysis_service.close()
            await app_recording_manager.close()
            logger.info("server_stopping")

    app = FastAPI(title="voicebot", lifespan=lifespan)
    app.include_router(
        build_dashboard_router(
            app_settings,
            app_call_manager,
            event_bus,
            app_recording_manager,
            test_campaign_manager,
            connection_registry=connection_registry,
        )
    )
    app.include_router(
        build_router(
            app_settings,
            app_call_manager,
            app_recording_manager,
            event_bus=event_bus,
            test_campaign_manager=test_campaign_manager,
            connection_registry=connection_registry,
        )
    )

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return HealthResponse()

    @app.post("/calls/outbound", response_model=OutboundCallResponse)
    async def create_outbound_call(request: OutboundCallRequest) -> OutboundCallResponse:
        try:
            metadata = app_call_manager.create_outbound_call(
                to_number=request.to_number,
                stream_base_url=request.stream_base_url,
                record_call=request.record_call,
                custom_parameters=request.custom_parameters,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TwilioClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        test_campaign_manager.activate_run(metadata)
        event_bus.begin_call(metadata.call_sid)
        return OutboundCallResponse(call_sid=metadata.call_sid, status=metadata.status)

    return app
