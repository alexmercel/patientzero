"""Transcript ownership agent used by the dashboard event bus."""

from __future__ import annotations

from typing import Any

from voicebot.src.agents.base import BaseAgent
from voicebot.src.observability.transcript_store import TranscriptStore


class TranscriptAgent(BaseAgent):
    """Owns live transcript session state and ingestion."""

    name = "transcript"

    def __init__(self) -> None:
        self._store = TranscriptStore()
        self._active_call_sid: str | None = None
        self._session_open = True

    def transcript_messages(self) -> list[dict[str, Any]]:
        return self._store.messages()

    def begin_call(self, call_sid: str) -> bool:
        clean_call_sid = str(call_sid).strip()
        if not clean_call_sid:
            return False

        should_reset = clean_call_sid != self._active_call_sid or self._store.has_messages()
        self._active_call_sid = clean_call_sid
        self._session_open = True
        if should_reset:
            self._store.reset()
        return should_reset

    def end_call(self, call_sid: str) -> bool:
        clean_call_sid = str(call_sid).strip()
        if (
            not clean_call_sid
            or clean_call_sid != self._active_call_sid
            or not self._session_open
        ):
            return False

        self._session_open = False
        if not self._store.has_messages():
            return False
        self._store.reset()
        return True

    def should_ingest(self, payload: dict[str, Any]) -> bool:
        event_name = str(payload.get("event") or "").strip()
        if event_name not in {"call_transcript_updated", "call_transcript_committed"}:
            return True

        if self._active_call_sid is None:
            return True

        call_sid = str(payload.get("call_sid") or "").strip()
        return bool(call_sid) and call_sid == self._active_call_sid and self._session_open

    def ingest_log_event(
        self,
        payload: dict[str, Any],
        *,
        fallback_timestamp: str,
    ) -> dict[str, Any] | None:
        if not self.should_ingest(payload):
            return None
        return self._store.ingest_log_event(payload, fallback_timestamp=fallback_timestamp)

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": "ready",
            "active_call_sid": self._active_call_sid,
            "message_count": len(self._store.messages()),
        }
