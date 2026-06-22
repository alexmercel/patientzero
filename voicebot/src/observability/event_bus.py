"""In-memory dashboard event bus."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from itertools import count
from typing import Any

from voicebot.src.agents.transcript_agent import TranscriptAgent


class DashboardEventBus:
    """Stores recent events and fans them out to live subscribers."""

    def __init__(
        self,
        max_events: int = 500,
        subscriber_queue_size: int = 200,
        transcript_agent: TranscriptAgent | None = None,
    ) -> None:
        self.max_events = max_events
        self.subscriber_queue_size = subscriber_queue_size
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._subscribers: dict[int, asyncio.Queue[dict[str, Any]]] = {}
        self._event_ids = count(1)
        self._subscriber_ids = count(1)
        self._transcript_agent = transcript_agent or TranscriptAgent()

    def publish(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Store an event and send it to all active subscribers."""
        event = {
            "id": next(self._event_ids),
            "kind": kind,
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": _json_safe(payload),
        }
        if kind == "log":
            transcript_update = None
            transcript_update = self._transcript_agent.ingest_log_event(
                event["payload"],
                fallback_timestamp=event["timestamp"],
            )
            if transcript_update is not None:
                event["payload"]["transcript_message"] = transcript_update["message"]
                if transcript_update["replaces_id"] is not None:
                    event["payload"]["transcript_replaces_id"] = transcript_update["replaces_id"]
        self._events.append(event)
        for queue in self._subscribers.values():
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
        return event

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return the most recent dashboard events."""
        if limit <= 0:
            return []
        return list(self._events)[-limit:]

    def subscribe(self) -> tuple[int, asyncio.Queue[dict[str, Any]]]:
        """Create a live queue for one websocket subscriber."""
        subscriber_id = next(self._subscriber_ids)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.subscriber_queue_size)
        self._subscribers[subscriber_id] = queue
        return subscriber_id, queue

    def unsubscribe(self, subscriber_id: int) -> None:
        """Remove a live subscriber queue."""
        self._subscribers.pop(subscriber_id, None)

    def transcript_messages(self) -> list[dict[str, Any]]:
        """Return the full stored transcript history."""
        return self._transcript_agent.transcript_messages()

    def begin_call(self, call_sid: str) -> dict[str, Any] | None:
        """Start a fresh transcript session for a newly-active call."""
        clean_call_sid = str(call_sid).strip()
        if not clean_call_sid:
            return None

        if not self._transcript_agent.begin_call(clean_call_sid):
            return None
        return self.publish(
            "state",
            {
                "event": "transcript_reset",
                "call_sid": clean_call_sid,
                "reason": "call_started",
            },
        )

    def end_call(self, call_sid: str) -> dict[str, Any] | None:
        """Mark the active transcript session as inactive when the call ends."""
        clean_call_sid = str(call_sid).strip()
        if not clean_call_sid:
            return None
        if not self._transcript_agent.end_call(clean_call_sid):
            return None
        return self.publish(
            "state",
            {
                "event": "transcript_reset",
                "call_sid": clean_call_sid,
                "reason": "call_ended",
            },
        )


def _json_safe(value: Any) -> Any:
    """Recursively convert values into JSON-safe payloads."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    return str(value)
