"""Bidirectional audio bridge between Twilio and Gemini Live."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any, Awaitable, Callable
from uuid import uuid4

from voicebot.src.config.settings import Settings
from voicebot.src.models.session_state import SessionState
from voicebot.src.models.twilio_stream import (
    TwilioMarkEvent,
    TwilioMediaEvent,
    TwilioOutboundClearMessage,
    TwilioOutboundMarkMessage,
    TwilioOutboundMarkPayload,
    TwilioOutboundMediaMessage,
    TwilioOutboundMediaPayload,
    TwilioStartEvent,
)
from voicebot.src.realtime.gemini_live_client import GeminiLiveClient
from voicebot.src.testing.campaign_manager import TestCampaignManager
from voicebot.src.utils.audio import (
    chunk_bytes,
    decode_base64_payload,
    encode_base64_payload,
    pcm16le_average_amplitude,
    pcm16le_to_ulaw_bytes,
    resample_pcm16le,
    ulaw_bytes_to_pcm16le,
)
from voicebot.src.utils.logging import get_logger

TWILIO_OUTBOUND_CHUNK_SIZE = 160
GEMINI_INPUT_SAMPLE_RATE = 16000


class AudioBridge:
    """Owns the live audio bridge for a single phone call."""

    def __init__(
        self,
        session_state: SessionState,
        settings: Settings,
        gemini_client: GeminiLiveClient,
        send_twilio_message: Callable[[Any], Awaitable[None]],
        test_campaign_manager: TestCampaignManager | None = None,
    ) -> None:
        self.session_state = session_state
        self.settings = settings
        self.gemini_client = gemini_client
        self.send_twilio_message = send_twilio_message
        self.test_campaign_manager = test_campaign_manager
        self.logger = get_logger(__name__)
        self._receive_task: asyncio.Task[None] | None = None
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._pending_user_transcript = ""
        self._pending_agent_transcript = ""
        self._suppress_input_until = 0.0
        self._last_suppression_log_at = 0.0
        self._pending_playback_marks: set[str] = set()
        self._barge_in_frame_count = 0
        self._pending_transition_nudge: str | None = None
        self._transition_nudge_task: asyncio.Task[None] | None = None
        self._last_representative_activity_at = 0.0
        self._representative_hold_active = False

    async def start(self, event: TwilioStartEvent) -> None:
        """Start Gemini connectivity after Twilio announces the stream."""
        self.session_state.attach_stream(event.start.stream_sid)
        self.session_state.mark_active()
        self.gemini_client.bind_context(
            call_sid=event.start.call_sid,
            stream_sid=event.start.stream_sid,
            session_id=self.session_state.session_id,
        )
        await self.gemini_client.connect()
        self._receive_task = asyncio.create_task(self._forward_gemini_audio())
        self.logger.info(
            "audio_bridge_started",
            call_sid=event.start.call_sid,
            stream_sid=event.start.stream_sid,
            sample_rate=event.start.media_format.sample_rate,
            encoding=event.start.media_format.encoding,
        )

    async def handle_twilio_media(self, event: TwilioMediaEvent) -> None:
        """Decode Twilio media and forward it to Gemini."""
        if self._closed:
            return
        ulaw_payload = decode_base64_payload(event.media.payload)
        pcm_payload = ulaw_bytes_to_pcm16le(ulaw_payload)
        gemini_payload = resample_pcm16le(
            pcm_payload,
            input_rate=8000,
            output_rate=GEMINI_INPUT_SAMPLE_RATE,
        )
        self.session_state.record_twilio_inbound(len(ulaw_payload))
        amplitude = pcm16le_average_amplitude(pcm_payload)
        now = monotonic()
        if amplitude >= self.settings.representative_activity_amplitude_threshold:
            self._last_representative_activity_at = now
            self._reschedule_transition_nudge_if_waiting()

        if now < self._suppress_input_until:
            if self._should_barge_in(amplitude):
                self._barge_in_frame_count += 1
                if self._barge_in_frame_count >= self.settings.twilio_barge_in_min_consecutive_frames:
                    await self._clear_twilio_playback(reason="caller_barge_in")
                    self._suppress_input_until = 0.0
                    self._barge_in_frame_count = 0
                    self.logger.info(
                        "audio_bridge_caller_barge_in_detected",
                        call_sid=self.session_state.call_sid,
                        stream_sid=event.stream_sid,
                        amplitude=round(amplitude, 2),
                    )
                else:
                    return
            else:
                self._barge_in_frame_count = 0
                if now - self._last_suppression_log_at >= 1.0:
                    self.logger.info(
                        "audio_bridge_twilio_media_suppressed",
                        call_sid=self.session_state.call_sid,
                        stream_sid=event.stream_sid,
                        suppress_for_ms=int((self._suppress_input_until - now) * 1000),
                        amplitude=round(amplitude, 2),
                    )
                    self._last_suppression_log_at = now
                return
        else:
            self._barge_in_frame_count = 0

        self.session_state.record_gemini_outbound(len(gemini_payload))
        await self.gemini_client.send_audio(
            gemini_payload,
            sample_rate=GEMINI_INPUT_SAMPLE_RATE,
        )
        self.logger.debug(
            "audio_bridge_twilio_media_forwarded",
            call_sid=self.session_state.call_sid,
            stream_sid=event.stream_sid,
            twilio_bytes=len(ulaw_payload),
            twilio_pcm_bytes=len(pcm_payload),
            gemini_bytes=len(gemini_payload),
            gemini_sample_rate=GEMINI_INPUT_SAMPLE_RATE,
        )

    async def close(self) -> None:
        """Stop the bridge and release resources."""
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            if self._receive_task is not None:
                self._receive_task.cancel()
                try:
                    await self._receive_task
                except asyncio.CancelledError:
                    pass
                self._receive_task = None
            if self._transition_nudge_task is not None:
                self._transition_nudge_task.cancel()
                try:
                    await self._transition_nudge_task
                except asyncio.CancelledError:
                    pass
                self._transition_nudge_task = None
            await self.gemini_client.disconnect()
            self.session_state.mark_completed()
            self.logger.info(
                "audio_bridge_closed",
                call_sid=self.session_state.call_sid,
                stream_sid=self.session_state.stream_sid,
                bytes_from_twilio=self.session_state.bytes_from_twilio,
                bytes_to_twilio=self.session_state.bytes_to_twilio,
                bytes_to_gemini=self.session_state.bytes_to_gemini,
                bytes_from_gemini=self.session_state.bytes_from_gemini,
            )

    async def handle_twilio_mark(self, event: TwilioMarkEvent) -> None:
        """Track Twilio playback acknowledgements for previously-sent audio."""
        if event.mark.name not in self._pending_playback_marks:
            self.logger.info(
                "audio_bridge_twilio_mark_ignored",
                call_sid=self.session_state.call_sid,
                stream_sid=event.stream_sid,
                mark_name=event.mark.name,
            )
            return

        self._pending_playback_marks.discard(event.mark.name)
        if not self._pending_playback_marks:
            self._suppress_input_until = 0.0

        self.logger.info(
            "audio_bridge_twilio_mark_acknowledged",
            call_sid=self.session_state.call_sid,
            stream_sid=event.stream_sid,
            mark_name=event.mark.name,
            playback_marks_pending=len(self._pending_playback_marks),
        )

    async def _forward_gemini_audio(self) -> None:
        try:
            async for event in self.gemini_client.receive_events():
                if event.go_away_time_left:
                    self.logger.warning(
                        "audio_bridge_gemini_go_away_received",
                        call_sid=self.session_state.call_sid,
                        stream_sid=self.session_state.stream_sid,
                        time_left=event.go_away_time_left,
                    )

                if event.interrupted:
                    interrupted_text = self._pending_agent_transcript
                    self._pending_agent_transcript = ""
                    self._suppress_input_until = 0.0
                    await self._clear_twilio_playback(reason="gemini_interrupted")
                    self.logger.info(
                        "audio_bridge_gemini_turn_interrupted",
                        call_sid=self.session_state.call_sid,
                        stream_sid=self.session_state.stream_sid,
                        partial_text=interrupted_text or None,
                    )

                if event.input_transcript:
                    self._last_representative_activity_at = monotonic()
                    self._reschedule_transition_nudge_if_waiting()
                    self._pending_user_transcript = _merge_transcript(
                        self._pending_user_transcript,
                        event.input_transcript,
                    )
                    await self._maybe_enter_representative_hold(self._pending_user_transcript)
                    self.logger.info(
                        "call_transcript_updated",
                        call_sid=self.session_state.call_sid,
                        speaker="user",
                        text=self._pending_user_transcript,
                        turn_complete=False,
                    )

                if event.transcript:
                    if self._pending_transition_nudge:
                        self._clear_pending_transition_nudge()
                    self._pending_agent_transcript = _merge_transcript(
                        self._pending_agent_transcript,
                        event.transcript,
                    )
                    self.logger.info(
                        "call_transcript_updated",
                        call_sid=self.session_state.call_sid,
                        speaker="agent",
                        text=self._pending_agent_transcript,
                        turn_complete=event.turn_complete,
                    )

                if event.turn_complete:
                    progress_dirty = False
                    transition_nudge: str | None = None
                    if self._pending_user_transcript:
                        self.logger.info(
                            "call_transcript_committed",
                            call_sid=self.session_state.call_sid,
                            speaker="user",
                            text=self._pending_user_transcript,
                        )
                        self.session_state.append_transcript_turn("user", self._pending_user_transcript)
                        self.gemini_client.remember_user_turn(self._pending_user_transcript)
                        self._update_representative_hold_state(self._pending_user_transcript)
                        self._pending_user_transcript = ""
                        progress_dirty = True
                    if self._pending_agent_transcript:
                        self.logger.info(
                            "call_transcript_committed",
                            call_sid=self.session_state.call_sid,
                            speaker="agent",
                            text=self._pending_agent_transcript,
                        )
                        self.session_state.append_transcript_turn("agent", self._pending_agent_transcript)
                        self.gemini_client.remember_agent_turn(self._pending_agent_transcript)
                        self._pending_agent_transcript = ""
                        progress_dirty = True
                    if progress_dirty and self.test_campaign_manager is not None:
                        if hasattr(self.test_campaign_manager, "process_live_turns"):
                            transition_nudge = self.test_campaign_manager.process_live_turns(self.session_state)
                        if hasattr(self.test_campaign_manager, "update_live_progress"):
                            self.test_campaign_manager.update_live_progress(self.session_state)
                    if transition_nudge and self.session_state.call_sid:
                        self._queue_transition_nudge(transition_nudge)

                if event.generation_complete:
                    self.logger.info(
                        "audio_bridge_gemini_generation_complete",
                        call_sid=self.session_state.call_sid,
                        stream_sid=self.session_state.stream_sid,
                    )

                if not event.audio or self.session_state.stream_sid is None:
                    continue

                self.session_state.record_gemini_inbound(len(event.audio))
                resampled = resample_pcm16le(event.audio, input_rate=24000, output_rate=8000)
                ulaw_payload = pcm16le_to_ulaw_bytes(resampled)
                # Prevent the model from hearing its own synthesized voice reflected
                # back through the phone mic while this utterance is still playing.
                playback_seconds = len(ulaw_payload) / 8000.0
                self._suppress_input_until = max(
                    self._suppress_input_until,
                    monotonic() + playback_seconds + 0.35,
                )
                for chunk in chunk_bytes(ulaw_payload, TWILIO_OUTBOUND_CHUNK_SIZE):
                    self.session_state.record_twilio_outbound(len(chunk))
                    message = TwilioOutboundMediaMessage(
                        streamSid=self.session_state.stream_sid,
                        media=TwilioOutboundMediaPayload(payload=encode_base64_payload(chunk)),
                    )
                    await self.send_twilio_message(message)

                mark_name = f"gemini-turn-{uuid4().hex[:12]}"
                self._pending_playback_marks.add(mark_name)
                await self.send_twilio_message(
                    TwilioOutboundMarkMessage(
                        streamSid=self.session_state.stream_sid,
                        mark=TwilioOutboundMarkPayload(name=mark_name),
                    )
                )

                self.logger.debug(
                    "audio_bridge_gemini_media_forwarded",
                    call_sid=self.session_state.call_sid,
                    stream_sid=self.session_state.stream_sid,
                    gemini_bytes=len(event.audio),
                    twilio_bytes=len(ulaw_payload),
                    turn_complete=event.turn_complete,
                    mark_name=mark_name,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception(
                "audio_bridge_gemini_receive_failed",
                call_sid=self.session_state.call_sid,
                stream_sid=self.session_state.stream_sid,
            )
            raise

    async def _clear_twilio_playback(self, reason: str) -> None:
        if self.session_state.stream_sid is None or not self._pending_playback_marks:
            return

        await self.send_twilio_message(
            TwilioOutboundClearMessage(streamSid=self.session_state.stream_sid)
        )
        self.logger.info(
            "audio_bridge_twilio_playback_cleared",
            call_sid=self.session_state.call_sid,
            stream_sid=self.session_state.stream_sid,
            reason=reason,
            playback_marks_pending=len(self._pending_playback_marks),
        )

    def _should_barge_in(self, amplitude: float) -> bool:
        return (
            bool(self._pending_playback_marks)
            and amplitude >= self.settings.twilio_barge_in_amplitude_threshold
        )

    def _queue_transition_nudge(self, nudge: str) -> None:
        self._pending_transition_nudge = nudge
        self._last_representative_activity_at = max(self._last_representative_activity_at, monotonic())
        self._restart_transition_nudge_task()

    def _reschedule_transition_nudge_if_waiting(self) -> None:
        if self._pending_transition_nudge is None:
            return
        self._restart_transition_nudge_task()

    def _clear_pending_transition_nudge(self) -> None:
        self._pending_transition_nudge = None
        if self._transition_nudge_task is not None:
            self._transition_nudge_task.cancel()
            self._transition_nudge_task = None

    def _restart_transition_nudge_task(self) -> None:
        if self._transition_nudge_task is not None:
            self._transition_nudge_task.cancel()
        self._transition_nudge_task = asyncio.create_task(self._deliver_transition_nudge_when_settled())

    async def _deliver_transition_nudge_when_settled(self) -> None:
        try:
            while not self._closed and self._pending_transition_nudge:
                idle_for = monotonic() - self._last_representative_activity_at
                remaining = self.settings.representative_turn_settle_seconds - idle_for
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue
                if self.session_state.call_sid and self.test_campaign_manager is not None:
                    self.gemini_client.set_call_instruction(
                        self.test_campaign_manager.build_call_instruction(self.session_state.call_sid)
                    )
                nudge = self._pending_transition_nudge
                self._pending_transition_nudge = None
                if nudge:
                    await self.gemini_client.send_text_instruction(nudge)
                self._transition_nudge_task = None
                return
        except asyncio.CancelledError:
            raise

    async def _maybe_enter_representative_hold(self, representative_text: str) -> None:
        if self._representative_hold_active:
            return
        if not _represents_in_progress_support_turn(representative_text):
            return
        self._representative_hold_active = True
        await self.gemini_client.send_text_instruction(
            "[Hidden patient-direction note. Do not say this note aloud.] "
            "The representative is still checking the current issue. Do not speak, ask a new question, or repeat the scenario yet. "
            "Wait silently unless the representative asks you a direct question."
        )

    def _update_representative_hold_state(self, representative_text: str) -> None:
        if _represents_in_progress_support_turn(representative_text):
            self._representative_hold_active = True
            return
        if _represents_direct_support_question(representative_text):
            self._representative_hold_active = False
            return
        if representative_text.strip():
            self._representative_hold_active = False


def _merge_transcript(existing: str, incoming: str) -> str:
    """Merge cumulative or delta transcript updates into one coherent turn."""
    clean_existing = existing.strip()
    clean_incoming = incoming.strip()
    if not clean_existing:
        return clean_incoming
    if not clean_incoming:
        return clean_existing
    if clean_existing == clean_incoming:
        return clean_existing
    if clean_incoming.startswith(clean_existing):
        return clean_incoming
    if clean_existing.startswith(clean_incoming):
        return clean_existing
    if clean_incoming in clean_existing:
        return clean_existing

    overlap = _find_suffix_prefix_overlap(clean_existing, clean_incoming)
    if overlap > 0:
        return clean_existing + clean_incoming[overlap:]

    separator = "" if _should_join_without_space(clean_existing, clean_incoming) else " "
    return clean_existing + separator + clean_incoming


def _find_suffix_prefix_overlap(existing: str, incoming: str) -> int:
    max_overlap = min(len(existing), len(incoming))
    for size in range(max_overlap, 0, -1):
        if existing[-size:].lower() == incoming[:size].lower():
            return size
    return 0


def _should_join_without_space(existing: str, incoming: str) -> bool:
    if not existing or not incoming:
        return True
    if existing[-1].isspace() or incoming[0].isspace():
        return True
    if incoming[0] in ",.!?;:)]}":
        return True
    if existing[-1] in "([{/$#@":
        return True
    return False


def _represents_in_progress_support_turn(text: str) -> bool:
    lowered = " ".join(text.lower().split()).strip()
    if not lowered:
        return False
    markers = (
        "one moment",
        "let me check",
        "hold on",
        "please hold",
        "give me a moment",
        "just a moment",
        "pull that up",
        "pulling that up",
        "looking that up",
        "looking into that",
        "bear with me",
        "while i check",
        "while i look",
    )
    return any(marker in lowered for marker in markers)


def _represents_direct_support_question(text: str) -> bool:
    stripped = text.strip()
    lowered = " ".join(stripped.lower().split()).strip()
    if not lowered:
        return False
    if stripped.endswith("?"):
        return True
    question_starts = (
        "can you",
        "could you",
        "what is",
        "what's",
        "which",
        "when",
        "where",
        "who",
        "would you",
        "do you",
        "did you",
        "is it",
        "is that",
        "are you",
    )
    return lowered.startswith(question_starts)
