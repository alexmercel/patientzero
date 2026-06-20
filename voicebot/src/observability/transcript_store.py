"""Conversation transcript state derived from structured logs."""

from __future__ import annotations

from collections import defaultdict
from itertools import count
from typing import Any


class TranscriptStore:
    """Accumulates committed transcript turns and in-flight partials."""

    def __init__(self) -> None:
        self._sequence = count(1)
        self._commit_counters: dict[tuple[str, str], int] = defaultdict(int)
        self._committed_messages: list[dict[str, Any]] = []
        self._partial_messages: dict[tuple[str, str], dict[str, Any]] = {}

    def ingest_log_event(
        self,
        payload: dict[str, Any],
        *,
        fallback_timestamp: str,
    ) -> dict[str, Any] | None:
        """Update transcript state from a structured log payload."""
        event_name = str(payload.get("event") or "").strip()
        call_sid = str(payload.get("call_sid") or "").strip()
        speaker = str(payload.get("speaker") or "").strip()
        text = str(payload.get("text") or "")
        timestamp = str(payload.get("timestamp") or fallback_timestamp)

        if not call_sid or speaker not in {"user", "agent"} or not text:
            return None

        key = (call_sid, speaker)
        if event_name == "call_transcript_updated":
            message = self._partial_messages.get(key)
            if message is None:
                message = {
                    "id": f"{call_sid}:{speaker}:partial",
                    "call_sid": call_sid,
                    "speaker": speaker,
                    "text": text,
                    "committed": False,
                    "updated_at": timestamp,
                    "sequence": next(self._sequence),
                }
                self._partial_messages[key] = message
            else:
                message["text"] = text
                message["updated_at"] = timestamp
            return {"message": dict(message), "replaces_id": None}

        if event_name != "call_transcript_committed":
            return None

        existing_partial = self._partial_messages.pop(key, None)
        self._commit_counters[key] += 1
        message = {
            "id": f"{call_sid}:{speaker}:commit:{self._commit_counters[key]}",
            "call_sid": call_sid,
            "speaker": speaker,
            "text": text,
            "committed": True,
            "updated_at": timestamp,
            "sequence": (
                existing_partial["sequence"]
                if existing_partial is not None
                else next(self._sequence)
            ),
        }
        self._committed_messages.append(message)
        return {
            "message": dict(message),
            "replaces_id": existing_partial["id"] if existing_partial is not None else None,
        }

    def messages(self) -> list[dict[str, Any]]:
        """Return all transcript messages in conversation order."""
        messages = self._committed_messages + list(self._partial_messages.values())
        return sorted(messages, key=lambda item: (item["sequence"], item["updated_at"]))

    def has_messages(self) -> bool:
        """Return whether any transcript content is currently stored."""
        return bool(self._committed_messages or self._partial_messages)

    def reset(self) -> None:
        """Clear transcript history and restart local sequencing."""
        self._sequence = count(1)
        self._commit_counters.clear()
        self._committed_messages.clear()
        self._partial_messages.clear()
