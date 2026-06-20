"""Post-call deep analysis using Gemini Flash on raw audio."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from voicebot.src.config.settings import Settings
from voicebot.src.utils.logging import get_logger

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover - dependency guard
    genai = None
    genai_types = None

if TYPE_CHECKING:
    from voicebot.src.testing.campaign_manager import RecordingReference, TestRunRecord


class DeepAnalysisServiceError(RuntimeError):
    """Raised when deep analysis cannot be performed."""


class DeepAnalysisService:
    """Schedules bounded background deep-analysis tasks for completed calls."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self.logger = get_logger(__name__)
        self._client = client or self._build_client()
        self._tasks: set[asyncio.Task[None]] = set()
        self._scheduled_files: set[str] = set()

    def _build_client(self) -> Any:
        if genai is None:
            raise DeepAnalysisServiceError("google-genai package is not installed.")
        return genai.Client(api_key=self.settings.gemini_api_key)

    def schedule_run_analysis(self, run: "TestRunRecord") -> None:
        """Schedule deep analysis for all recordings on a completed run."""
        if not self.settings.gemini_deep_analysis_enabled or run.status != "completed":
            return
        for recording in run.recordings:
            self._schedule_recording(run, recording)

    def _schedule_recording(self, run: "TestRunRecord", recording: "RecordingReference") -> None:
        recording_path = str(recording.recording_path or "").strip()
        filename = str(recording.filename or "").strip()
        if not recording_path or not filename:
            return

        deep_analysis_path = self.settings.recordings_dir / _deep_analysis_filename_from_recording(filename)
        if deep_analysis_path.is_file():
            return
        if filename in self._scheduled_files:
            return

        self._scheduled_files.add(filename)
        task = asyncio.create_task(self._run_analysis_task(run, recording, deep_analysis_path))
        self._tasks.add(task)
        task.add_done_callback(lambda finished: self._finalize_task(filename, finished))

    def _finalize_task(self, filename: str, task: asyncio.Task[None]) -> None:
        self._scheduled_files.discard(filename)
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            self.logger.exception("deep_analysis_task_failed", filename=filename)

    async def _run_analysis_task(
        self,
        run: "TestRunRecord",
        recording: "RecordingReference",
        output_path: Path,
    ) -> None:
        await asyncio.wait_for(
            self._write_deep_analysis_report(run, recording, output_path),
            timeout=self.settings.gemini_deep_analysis_timeout_seconds,
        )

    async def _write_deep_analysis_report(
        self,
        run: "TestRunRecord",
        recording: "RecordingReference",
        output_path: Path,
    ) -> None:
        recording_path = Path(str(recording.recording_path))
        if not recording_path.is_file():
            self.logger.warning("deep_analysis_recording_missing", path=str(recording_path))
            return
        if genai_types is None:
            raise DeepAnalysisServiceError("google-genai package is not installed.")

        prompt = _build_deep_analysis_prompt(run, recording)
        audio_bytes = await asyncio.to_thread(self._load_audio_bytes_for_analysis, recording_path)
        audio_part = genai_types.Part.from_bytes(
            data=audio_bytes,
            mime_type=_mime_type_for_recording(recording_path),
        )
        self.logger.info(
            "deep_analysis_started",
            call_sid=run.call_sid,
            run_id=run.run_id,
            filename=recording.filename,
            model=self.settings.gemini_deep_analysis_model,
        )
        response, model_used = await self._generate_content(prompt, audio_part)
        text = getattr(response, "text", None) or _flatten_candidate_text(response)
        if not text.strip():
            raise DeepAnalysisServiceError("Gemini deep analysis returned no text.")

        output_path.write_text(
            _format_deep_analysis_markdown(run, recording, text.strip(), model_used),
            encoding="utf-8",
        )
        self.logger.info(
            "deep_analysis_completed",
            call_sid=run.call_sid,
            run_id=run.run_id,
            filename=recording.filename,
            model=model_used,
            output_path=str(output_path),
        )

    def _load_audio_bytes_for_analysis(self, recording_path: Path) -> bytes:
        trim_seconds = max(0.0, self.settings.gemini_deep_analysis_trim_lead_seconds)
        if trim_seconds <= 0:
            return recording_path.read_bytes()

        trimmed_audio = self._trim_leading_audio_bytes(recording_path, trim_seconds)
        if trimmed_audio:
            return trimmed_audio
        return recording_path.read_bytes()

    def _trim_leading_audio_bytes(self, recording_path: Path, trim_seconds: float) -> bytes | None:
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            self.logger.warning(
                "deep_analysis_trim_skipped_ffmpeg_missing",
                path=str(recording_path),
                trim_seconds=trim_seconds,
            )
            return None

        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{trim_seconds:.3f}",
            "-i",
            str(recording_path),
            "-map",
            "0:a:0",
            "-f",
            "mp3",
            "pipe:1",
        ]
        try:
            result = subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError):
            self.logger.warning(
                "deep_analysis_trim_failed",
                path=str(recording_path),
                trim_seconds=trim_seconds,
            )
            return None

        if not result.stdout:
            self.logger.warning(
                "deep_analysis_trim_empty_output",
                path=str(recording_path),
                trim_seconds=trim_seconds,
            )
            return None

        self.logger.info(
            "deep_analysis_trim_applied",
            path=str(recording_path),
            trim_seconds=trim_seconds,
            output_bytes=len(result.stdout),
        )
        return result.stdout

    async def _generate_content(self, prompt: str, audio_part: Any) -> tuple[Any, str]:
        primary_model = self.settings.gemini_deep_analysis_model
        fallback_model = self.settings.gemini_deep_analysis_fallback_model.strip()
        try:
            return await self._generate_content_for_model(primary_model, prompt, audio_part), primary_model
        except Exception:
            if not fallback_model or fallback_model == primary_model:
                raise
            self.logger.warning(
                "deep_analysis_primary_model_failed_falling_back",
                primary_model=primary_model,
                fallback_model=fallback_model,
            )
            return await self._generate_content_for_model(fallback_model, prompt, audio_part), fallback_model

    async def _generate_content_for_model(self, model: str, prompt: str, audio_part: Any) -> Any:
        if hasattr(self._client, "aio") and hasattr(self._client.aio, "models"):
            return await self._client.aio.models.generate_content(
                model=model,
                contents=[prompt, audio_part],
            )
        return await asyncio.to_thread(
            self._client.models.generate_content,
            model=model,
            contents=[prompt, audio_part],
        )

    async def close(self) -> None:
        """Cancel or await deep-analysis tasks with a bounded timeout."""
        if not self._tasks:
            return
        pending = list(self._tasks)
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=min(5.0, self.settings.gemini_deep_analysis_timeout_seconds),
            )
        except TimeoutError:
            self.logger.warning("deep_analysis_close_timed_out", task_count=len(pending))
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


def _deep_analysis_filename_from_recording(filename: str) -> str:
    return f"{Path(filename).stem}.deep-analysis.md"


def _mime_type_for_recording(recording_path: Path) -> str:
    if recording_path.suffix.lower() == ".wav":
        return "audio/wav"
    return "audio/mpeg"


def _build_deep_analysis_prompt(run: "TestRunRecord", recording: "RecordingReference") -> str:
    lines = [
        "You are a senior QA engineer reviewing a production healthcare voice agent.",
        "Analyze this call recording.",
        "Listen directly to the attached audio file.",
        "Do not ask for a transcript and do not rely on any externally produced transcript.",
        "Use the scenario metadata and expected behavior below to judge what happened in the audio.",
        "Be conservative and do not invent bugs.",
        "",
        "Look for:",
        "- hallucinations",
        "- incorrect office hours",
        "- duplicate appointments",
        "- doctor preference failures",
        "- pharmacy mismatches",
        "- insurance mistakes",
        "- interruptions",
        "- long pauses",
        "- emotional failures",
        "- context loss",
        "- identity leakage",
        "- emergency escalation failures",
        "- inappropriate transfer to human representative",
        "- date and time reasoning errors",
        "- multilingual failures",
        "- contradictory responses",
        "",
        "For every issue found provide:",
        "- timestamp",
        "- severity",
        "- confidence",
        "- why this is problematic",
        "- expected behavior",
        "",
        "Output format requirements:",
        "- Start with an Overall Verdict section.",
        "- Include a Bug Findings section ordered by severity.",
        "- Include a Scenario-by-Scenario Assessment section.",
        "- Include a Conversational Flow Analysis section.",
        "- Include a Recommended Fixes section.",
        "- If no bug is found for a category, do not fabricate one.",
        "- Quote very short snippets only when needed; otherwise paraphrase.",
        "",
        f"Run ID: {run.run_id}",
        f"Call SID: {run.call_sid or 'unknown'}",
        f"Recording file: {recording.filename}",
        f"Persona: {run.persona_name} ({run.persona_id})",
        "",
        "Scenarios tested for this call:",
    ]
    for index, scenario in enumerate(run.scenario_results, start=1):
        lines.extend(
            [
                f"{index}. {scenario.scenario_id} | {scenario.category} | {scenario.title}",
                f"   Expected: {scenario.expected or 'Not specified'}",
                f"   Existing automatic outcome: {scenario.outcome if scenario.tested else 'pending'}",
                f"   Existing automatic details: {scenario.details}",
            ]
        )
        if scenario.ask:
            lines.append("   Patient ask lines:")
            for ask_line in scenario.ask:
                lines.append(f"   - {ask_line}")
    lines.extend(
        [
            "",
            "Judge the call using the audio itself. If the automatic outcome appears wrong based on the audio, say so explicitly.",
            "If the call reveals additional bugs beyond the scripted scenarios, include them.",
            "Pay close attention to whether the support agent pauses, types, or continues in multiple parts before completing an answer.",
            "Flag only issues that are supported by the audio with reasonable confidence.",
        ]
    )
    return "\n".join(lines)


def _flatten_candidate_text(response: Any) -> str:
    candidates = getattr(response, "candidates", None) or []
    fragments: list[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                fragments.append(text)
    return "\n".join(fragment.strip() for fragment in fragments if fragment and fragment.strip())


def _format_deep_analysis_markdown(
    run: "TestRunRecord",
    recording: "RecordingReference",
    analysis_text: str,
    model_used: str,
) -> str:
    lines = [
        f"# Deep Analysis - {recording.display_name or Path(recording.filename).stem}",
        "",
        f"- Recording: {recording.filename}",
        f"- Deep analysis file: {_deep_analysis_filename_from_recording(recording.filename)}",
        f"- Model used: {model_used}",
        f"- Run ID: {run.run_id}",
        f"- Call SID: {run.call_sid or 'unknown'}",
        f"- Persona: {run.persona_name} ({run.persona_id})",
        "",
        analysis_text,
        "",
    ]
    return "\n".join(lines)
