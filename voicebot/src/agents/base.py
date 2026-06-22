"""Shared contracts for deterministic PatientZero agents."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentEvent(BaseModel):
    """Typed event envelope passed between in-process agents."""

    type: str
    source: str
    call_sid: str | None = None
    run_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class BaseAgent:
    """Base lifecycle and event contract for deterministic agents."""

    name = "base"

    async def start(self) -> None:
        """Start background resources for this agent."""

    async def stop(self) -> None:
        """Stop background resources for this agent."""

    async def handle_event(self, event: AgentEvent) -> None:
        """Handle an event published by another agent."""

    def status(self) -> dict[str, Any]:
        """Return dashboard-safe agent status."""
        return {"name": self.name, "status": "ready"}


def json_safe(value: Any) -> Any:
    """Recursively convert values into JSON-safe payloads."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return json_safe(value.model_dump(mode="json"))
    return str(value)
