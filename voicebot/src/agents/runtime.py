"""Small in-process event runtime for deterministic agents."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from voicebot.src.agents.base import AgentEvent, BaseAgent, json_safe

Subscriber = Callable[[AgentEvent], Awaitable[None] | None]


class AgentRuntime:
    """Registers agents and fans typed events out to subscribers."""

    def __init__(self, dashboard_event_bus: Any | None = None, max_events: int = 500) -> None:
        self.dashboard_event_bus = dashboard_event_bus
        self._agents: dict[str, BaseAgent] = {}
        self._subscribers: dict[str, list[Subscriber]] = defaultdict(list)
        self._events: deque[AgentEvent] = deque(maxlen=max_events)
        self._lock = asyncio.Lock()
        self._started = False

    def register(self, agent: BaseAgent) -> BaseAgent:
        """Register one agent by name."""
        self._agents[agent.name] = agent
        return agent

    def agent(self, name: str) -> BaseAgent | None:
        """Look up a registered agent."""
        return self._agents.get(name)

    def subscribe(self, event_type: str, handler: Subscriber) -> None:
        """Subscribe a handler to an event type, or '*' for all events."""
        self._subscribers[event_type].append(handler)

    async def start(self) -> None:
        """Start all registered agents once."""
        if self._started:
            return
        self._started = True
        for agent in self._agents.values():
            await agent.start()

    async def stop(self) -> None:
        """Stop all registered agents in reverse registration order."""
        if not self._started:
            return
        for agent in reversed(list(self._agents.values())):
            await agent.stop()
        self._started = False

    async def publish(self, event: AgentEvent) -> AgentEvent:
        """Store an event, mirror it to the dashboard, and notify subscribers."""
        safe_event = AgentEvent(
            type=event.type,
            source=event.source,
            call_sid=event.call_sid,
            run_id=event.run_id,
            payload=json_safe(event.payload),
            timestamp=event.timestamp,
        )
        async with self._lock:
            self._events.append(safe_event)

        if self.dashboard_event_bus is not None:
            self.dashboard_event_bus.publish(
                "agent",
                {
                    "event": safe_event.type,
                    "source": safe_event.source,
                    "call_sid": safe_event.call_sid,
                    "run_id": safe_event.run_id,
                    "payload": safe_event.payload,
                    "timestamp": safe_event.timestamp,
                },
            )

        handlers = [
            *self._subscribers.get(safe_event.type, []),
            *self._subscribers.get("*", []),
        ]
        for handler in handlers:
            result = handler(safe_event)
            if result is not None:
                await result
        return safe_event

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent agent events in JSON-safe form."""
        if limit <= 0:
            return []
        return [event.model_dump(mode="json") for event in list(self._events)[-limit:]]

    def status(self) -> dict[str, Any]:
        """Return runtime and agent health for the dashboard."""
        return {
            "started": self._started,
            "agent_count": len(self._agents),
            "agents": [agent.status() for agent in self._agents.values()],
            "recent_events": self.recent(limit=25),
        }
