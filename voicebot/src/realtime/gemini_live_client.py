"""Gemini Live SDK wrapper."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from voicebot.src.config.settings import Settings
from voicebot.src.utils.logging import get_logger

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover - exercised through dependency guards
    genai = None
    genai_types = None


class GeminiLiveClientError(RuntimeError):
    """Raised when Gemini Live cannot be reached or used."""


@dataclass(slots=True)
class GeminiLiveEvent:
    """Normalized Gemini event used by the audio bridge."""

    audio: bytes = b""
    transcript: str | None = None
    input_transcript: str | None = None
    turn_complete: bool = False
    interrupted: bool = False
    generation_complete: bool = False
    go_away_time_left: str | None = None
    raw_message: Any | None = None


class GeminiLiveClient:
    """Small wrapper around the official `google-genai` live SDK."""

    def __init__(
        self,
        settings: Settings,
        client: Any | None = None,
        blob_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self.logger = get_logger(__name__)
        self._client = client or self._build_client()
        self._blob_factory = blob_factory or self._build_blob_factory()
        self._session_lock = asyncio.Lock()
        self._connect_cm: Any | None = None
        self._session: Any | None = None
        self._session_handle: str | None = None
        self._closed = False
        self._recent_turns: list[tuple[str, str]] = []
        self._pending_agent_question: str | None = None
        self._go_away_time_left: str | None = None
        self._last_user_turn: str | None = None
        self._consecutive_repeated_user_turns = 0
        self._recent_user_facts: list[str] = []
        self._call_instruction: str | None = None

    def _build_client(self) -> Any:
        if genai is None:
            raise GeminiLiveClientError("google-genai package is not installed.")
        return genai.Client(api_key=self.settings.gemini_api_key)

    def _build_blob_factory(self) -> Callable[..., Any]:
        if genai_types is None:
            raise GeminiLiveClientError("google-genai package is not installed.")
        return genai_types.Blob

    async def connect(self) -> None:
        """Open a Gemini Live session if one is not already active."""
        async with self._session_lock:
            if self._session is not None:
                return

            try:
                self._connect_cm = self._client.aio.live.connect(
                    model=self.settings.gemini_model,
                    config=self._build_connect_config(),
                )
                self._session = await self._connect_cm.__aenter__()
                self._closed = False
                self._go_away_time_left = None
                self.logger.info(
                    "gemini_session_opened",
                    model=self.settings.gemini_model,
                    voice_name=self.settings.gemini_voice_name,
                    resumed=bool(self._session_handle),
                )
            except Exception as exc:
                self._connect_cm = None
                self._session = None
                self.logger.exception("gemini_session_open_failed")
                raise GeminiLiveClientError("Failed to open Gemini Live session.") from exc

    def bind_context(self, **context: Any) -> None:
        """Attach stable call identifiers to subsequent structured logs."""
        self.logger = self.logger.bind(**context)

    def set_call_instruction(self, instruction: str | None) -> None:
        """Attach call-specific hidden instructions before a session starts."""
        self._call_instruction = instruction.strip() if instruction else None

    async def disconnect(self) -> None:
        """Close the current Gemini Live session."""
        self._closed = True
        await self._close_session(log_close=True)

    async def send_audio(self, pcm_bytes: bytes, sample_rate: int) -> None:
        """Send audio bytes to Gemini using the SDK realtime input API."""
        await self.connect()
        if self._session is None:
            raise GeminiLiveClientError("Gemini session is not connected.")

        try:
            blob = self._blob_factory(data=pcm_bytes, mime_type=f"audio/pcm;rate={sample_rate}")
            await self._session.send_realtime_input(audio=blob)
            self.logger.debug("gemini_audio_sent", bytes=len(pcm_bytes), sample_rate=sample_rate)
        except Exception as exc:
            self.logger.exception("gemini_send_audio_failed", sample_rate=sample_rate, retrying=True)
            await self._reset_session()
            try:
                await self.connect()
                if self._session is None:
                    raise GeminiLiveClientError("Gemini session is not connected after reconnect.")
                blob = self._blob_factory(data=pcm_bytes, mime_type=f"audio/pcm;rate={sample_rate}")
                await self._session.send_realtime_input(audio=blob)
                self.logger.debug(
                    "gemini_audio_sent_after_reconnect",
                    bytes=len(pcm_bytes),
                    sample_rate=sample_rate,
                )
            except Exception as retry_exc:
                self.logger.exception("gemini_send_audio_retry_failed", sample_rate=sample_rate)
                raise GeminiLiveClientError("Failed to send audio to Gemini Live.") from retry_exc

    async def send_text_instruction(self, text: str) -> None:
        """Send a short hidden realtime text nudge into the live session."""
        cleaned = text.strip()
        if not cleaned:
            return

        await self.connect()
        if self._session is None:
            raise GeminiLiveClientError("Gemini session is not connected.")

        try:
            await self._session.send_realtime_input(text=cleaned)
            self.logger.info("gemini_hidden_text_instruction_sent", characters=len(cleaned))
        except Exception as exc:
            self.logger.exception("gemini_hidden_text_instruction_failed", retrying=True)
            await self._reset_session()
            try:
                await self.connect()
                if self._session is None:
                    raise GeminiLiveClientError("Gemini session is not connected after reconnect.")
                await self._session.send_realtime_input(text=cleaned)
                self.logger.info("gemini_hidden_text_instruction_sent_after_reconnect", characters=len(cleaned))
            except Exception as retry_exc:
                self.logger.exception("gemini_hidden_text_instruction_retry_failed")
                raise GeminiLiveClientError("Failed to send hidden text instruction to Gemini Live.") from retry_exc

    async def receive_events(self) -> AsyncIterator[GeminiLiveEvent]:
        """Yield normalized events, reconnecting when the SDK stream fails."""
        attempts = 0

        while not self._closed:
            await self.connect()
            session = self._session
            if session is None:
                raise GeminiLiveClientError("Gemini session is not connected.")

            try:
                async for message in session.receive():
                    attempts = 0
                    self._update_session_handle(message)
                    event = self._extract_event(message)
                    if event.go_away_time_left:
                        self._go_away_time_left = event.go_away_time_left
                        self.logger.warning(
                            "gemini_go_away_received",
                            time_left=event.go_away_time_left,
                        )
                    if (
                        event.audio
                        or event.transcript
                        or event.input_transcript
                        or event.turn_complete
                        or event.interrupted
                        or event.generation_complete
                        or event.go_away_time_left
                    ):
                        yield event
                if not self._closed:
                    if self._go_away_time_left is not None:
                        raise GeminiLiveClientError("Gemini receive loop ended after go-away.")
                    raise GeminiLiveClientError("Gemini receive loop ended unexpectedly.")
            except Exception as exc:
                if self._closed:
                    break
                attempts += 1
                if self._go_away_time_left is not None:
                    self.logger.warning(
                        "gemini_receive_reconnect_after_go_away",
                        attempt=attempts,
                        time_left=self._go_away_time_left,
                    )
                else:
                    self.logger.exception("gemini_receive_failed", attempt=attempts)
                await self._reset_session()
                self._go_away_time_left = None
                if attempts > self.settings.gemini_max_reconnect_attempts:
                    raise GeminiLiveClientError("Gemini Live reconnect limit exceeded.") from exc
                await asyncio.sleep(self.settings.gemini_reconnect_backoff_seconds * attempts)

    async def _reset_session(self) -> None:
        await self._close_session(log_close=False)

    async def _close_session(self, *, log_close: bool) -> None:
        async with self._session_lock:
            connect_cm = self._connect_cm
            self._connect_cm = None
            self._session = None
            if connect_cm is None:
                return
            try:
                await asyncio.wait_for(connect_cm.__aexit__(None, None, None), timeout=5.0)
                if log_close:
                    self.logger.info("gemini_session_closed")
            except TimeoutError:
                if log_close:
                    self.logger.warning("gemini_session_close_timed_out")
                else:
                    self.logger.warning("gemini_session_reset_timed_out")

    def _build_connect_config(self) -> Any:
        if genai_types is None:
            raise GeminiLiveClientError("google-genai package is not installed.")
        return genai_types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            temperature=self.settings.gemini_temperature,
            system_instruction=self._build_system_instruction(),
            speech_config=genai_types.SpeechConfig(
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                        voice_name=self.settings.gemini_voice_name
                    )
                )
            ),
            session_resumption=genai_types.SessionResumptionConfig(
                handle=self._session_handle,
            ),
            context_window_compression=genai_types.ContextWindowCompressionConfig(
                sliding_window=genai_types.SlidingWindow()
            ),
            input_audio_transcription=genai_types.AudioTranscriptionConfig(),
            output_audio_transcription=genai_types.AudioTranscriptionConfig(),
        )

    def remember_user_turn(self, text: str) -> None:
        """Persist the latest committed representative turn for reconnect continuity."""
        normalized = _normalize_turn_text(text)
        if not normalized:
            return
        self._append_recent_turn("Representative", normalized)
        previous_turn = self._last_user_turn
        if previous_turn and _turns_are_materially_same(previous_turn, normalized):
            self._consecutive_repeated_user_turns += 1
        else:
            self._consecutive_repeated_user_turns = 1
        self._last_user_turn = normalized
        self._remember_user_fact(normalized)
        self._pending_agent_question = None

    def remember_agent_turn(self, text: str) -> None:
        """Persist the latest committed patient turn for reconnect continuity."""
        normalized = _normalize_turn_text(text)
        if not normalized:
            return
        self._append_recent_turn("Patient", normalized)
        self._pending_agent_question = normalized if normalized.endswith("?") else None

    def _append_recent_turn(self, speaker: str, text: str) -> None:
        self._recent_turns.append((speaker, text))
        if len(self._recent_turns) > 6:
            self._recent_turns = self._recent_turns[-6:]

    def _build_system_instruction(self) -> str:
        sections = [self.settings.gemini_system_instruction]
        if self._call_instruction:
            sections.append(self._call_instruction)
        note = self._build_continuation_note()
        if note:
            sections.append(note)
        return "\n\n".join(section for section in sections if section)

    def _build_continuation_note(self) -> str:
        if not self._recent_turns and not self._pending_agent_question:
            return ""

        lines = [
            "Call continuity note for a resumed connection:",
            "This is the same phone call already in progress.",
            "Stay in character as the patient, do not restart the scenario, and do not repeat generic pleasantries unless the representative explicitly asks in this turn.",
            "Continue naturally from the existing conversation state instead of recapping from scratch.",
        ]
        if self._recent_turns:
            lines.append("Recent committed turns:")
            for speaker, text in self._recent_turns[-4:]:
                lines.append(f"- {speaker}: {text}")
        if self._pending_agent_question:
            lines.append(f"Latest unresolved patient question: {self._pending_agent_question}")
        if self._last_user_turn:
            lines.append(f"Latest representative request or statement: {self._last_user_turn}")
        if self._consecutive_repeated_user_turns >= 2 and self._last_user_turn:
            lines.append(
                "The representative has effectively repeated the same answer twice in a row."
            )
            lines.append(
                "Do not ask for that same information a third time. Briefly acknowledge it and advance to the next relevant scenario or response."
            )
        if self._recent_user_facts:
            lines.append(
                "Facts the representative already provided and the patient should not ask to be repeated unless unclear:"
            )
            for fact in self._recent_user_facts[-4:]:
                lines.append(f"- {fact}")
        lines.append(
            "If the latest representative turn already contains enough information, respond directly as the patient instead of asking another question."
        )
        lines.append("Use this note only to preserve continuity. Do not read or summarize it aloud.")
        return "\n".join(lines)

    def _remember_user_fact(self, text: str) -> None:
        for sentence in _extract_candidate_facts(text):
            if sentence in self._recent_user_facts:
                continue
            self._recent_user_facts.append(sentence)
        if len(self._recent_user_facts) > 8:
            self._recent_user_facts = self._recent_user_facts[-8:]

    def _update_session_handle(self, message: Any) -> None:
        update = getattr(message, "session_resumption_update", None)
        if update is None:
            return
        resumable = bool(getattr(update, "resumable", False))
        new_handle = getattr(update, "new_handle", None) or getattr(update, "newHandle", None)
        if resumable and new_handle:
            self._session_handle = new_handle
            self.logger.info("gemini_session_handle_updated", resumable=True)

    def _extract_event(self, message: Any) -> GeminiLiveEvent:
        server_content = getattr(message, "server_content", None)
        if server_content is None:
            return GeminiLiveEvent(raw_message=message)

        transcript = None
        output_transcription = getattr(server_content, "output_transcription", None)
        if output_transcription is not None:
            transcript = getattr(output_transcription, "text", None)
        input_transcript = None
        input_transcription = getattr(server_content, "input_transcription", None)
        if input_transcription is not None:
            input_transcript = getattr(input_transcription, "text", None)
        go_away = getattr(message, "go_away", None)
        go_away_time_left = None
        if go_away is not None:
            go_away_time_left = getattr(go_away, "time_left", None) or getattr(go_away, "timeLeft", None)

        audio_chunks: list[bytes] = []
        model_turn = getattr(server_content, "model_turn", None)
        parts = getattr(model_turn, "parts", []) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None) or getattr(part, "inlineData", None)
            if inline_data is None:
                continue
            mime_type = getattr(inline_data, "mime_type", None) or getattr(inline_data, "mimeType", None)
            data = getattr(inline_data, "data", None)
            if not data or not mime_type or "audio/pcm" not in mime_type:
                continue
            if isinstance(data, str):
                decoded = base64.b64decode(data)
            else:
                decoded = data
            audio_chunks.append(decoded)

        return GeminiLiveEvent(
            audio=b"".join(audio_chunks),
            transcript=transcript,
            input_transcript=input_transcript,
            turn_complete=bool(getattr(server_content, "turn_complete", False)),
            interrupted=bool(getattr(server_content, "interrupted", False)),
            generation_complete=bool(getattr(server_content, "generation_complete", False)),
            go_away_time_left=go_away_time_left,
            raw_message=message,
        )


def _normalize_turn_text(text: str, max_chars: int = 240) -> str:
    """Collapse whitespace and cap stored continuity text."""
    normalized = " ".join(text.split()).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _extract_candidate_facts(text: str) -> list[str]:
    """Keep short declarative caller facts for reconnect continuity."""
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return []

    fragments = []
    for part in normalized.replace("?", ".").replace("!", ".").split("."):
        sentence = part.strip(" ,;:-")
        if not sentence:
            continue
        lowered = sentence.lower()
        if any(
            token in lowered
            for token in (
                "my name is",
                "i am ",
                "i'm ",
                "the company is",
                "we are",
                "we're ",
                "our hours",
                "the hours are",
                "open",
                "close",
                "available",
                "looking for",
                "need ",
                "want ",
                "prefer ",
                "can do",
                "cannot",
                "can't",
            )
        ):
            fragments.append(sentence)
            continue
        if any(char.isdigit() for char in sentence):
            fragments.append(sentence)
    return fragments[:4]


def _turns_are_materially_same(left: str, right: str) -> bool:
    normalized_left = _normalize_turn_text(left).lower()
    normalized_right = _normalize_turn_text(right).lower()
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    if normalized_left in normalized_right or normalized_right in normalized_left:
        return True

    left_tokens = {token for token in normalized_left.split() if len(token) > 2}
    right_tokens = {token for token in normalized_right.split() if len(token) > 2}
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens)
    baseline = max(1, min(len(left_tokens), len(right_tokens)))
    return overlap / baseline >= 0.85
