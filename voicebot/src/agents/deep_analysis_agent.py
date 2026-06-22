"""Deep analysis agent wrapper."""

from __future__ import annotations

from typing import Any

from voicebot.src.agents.base import BaseAgent


class DeepAnalysisAgent(BaseAgent):
    """Wraps the paid/fallback Gemini deep-analysis service."""

    name = "deep_analysis"

    def __init__(self, service: Any | None) -> None:
        self.service = service

    async def stop(self) -> None:
        if self.service is not None:
            await self.service.close()

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": "ready" if self.service is not None else "disabled",
        }
