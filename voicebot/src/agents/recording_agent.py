"""Recording management agent wrapper."""

from __future__ import annotations

from typing import Any

from voicebot.src.agents.base import BaseAgent


class RecordingAgent(BaseAgent):
    """Owns recording list, favorites, and artifact lookup behavior."""

    name = "recording"

    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def list_recordings(self) -> list[Any]:
        return self.manager.list_recordings()

    def update_favorite(self, filename: str, favorite: bool | None = None) -> Any:
        return self.manager.update_favorite(filename, favorite)

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": "ready",
            "recording_count": len(self.manager.list_recordings()),
        }
