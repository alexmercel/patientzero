"""Dashboard snapshot assembly agent."""

from __future__ import annotations

from typing import Any

from voicebot.src.agents.base import BaseAgent
from voicebot.src.config.settings import Settings


class DashboardAgent(BaseAgent):
    """Assembles the operator dashboard snapshot from manager-owned state."""

    name = "dashboard"

    def snapshot(
        self,
        *,
        settings: Settings,
        call_manager: Any,
        event_bus: Any,
        recording_manager: Any,
        test_campaign_manager: Any,
    ) -> dict[str, Any]:
        active_calls = [
            session.model_dump(mode="json")
            for session in call_manager.list_calls(active_only=True)
        ]
        recent_calls = [
            session.model_dump(mode="json")
            for session in call_manager.list_calls(active_only=False)
        ]
        recordings = [
            recording.model_dump(mode="json")
            for recording in recording_manager.list_recordings()
        ]
        return {
            "app": {
                "public_base_url": settings.public_base_url,
                "stream_url": _safe_url(settings),
                "active_call_count": len(call_manager.active_calls),
            },
            "events": event_bus.recent(limit=200),
            "transcript_messages": event_bus.transcript_messages(),
            "active_calls": active_calls,
            "recent_calls": recent_calls[:12],
            "recordings": recordings,
            "testing": test_campaign_manager.report_snapshot(recordings=recordings),
        }


def _safe_url(settings: Settings) -> str | None:
    try:
        return settings.build_stream_url()
    except ValueError:
        return None
