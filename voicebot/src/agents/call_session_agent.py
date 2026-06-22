"""Call session agent wrapper."""

from __future__ import annotations

from typing import Any

from voicebot.src.agents.base import BaseAgent


class CallSessionAgent(BaseAgent):
    """Owns call lifecycle delegation through the existing call manager."""

    name = "call_session"

    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def list_calls(self, *, active_only: bool = False) -> list[Any]:
        return self.manager.list_calls(active_only=active_only)

    def create_outbound_call(self, **kwargs: Any) -> Any:
        return self.manager.create_outbound_call(**kwargs)

    def hang_up_call(self, call_sid: str) -> Any:
        return self.manager.hang_up_call(call_sid)

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": "ready",
            "active_call_count": len(self.manager.active_calls),
        }
