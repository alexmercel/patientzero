"""Recording download and persistence helpers."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from voicebot.src.config.settings import Settings
from voicebot.src.models.api_models import RecordingSummary
from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.utils.logging import get_logger


class RecordingManagerError(RuntimeError):
    """Raised when recording persistence fails."""


class RecordingManager:
    """Downloads and stores Twilio recordings and sidecar metadata."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.logger = get_logger(__name__)
        self.http_client = http_client or httpx.AsyncClient(timeout=settings.request_timeout_seconds)

    def build_recording_filename(
        self,
        _call_sid: str,
        _recording_sid: str,
        extension: str = "mp3",
    ) -> str:
        """Create the next sequential recording filename."""
        stem = self.allocate_recording_stem()
        return f"{stem}.{extension}"

    def allocate_recording_stem(self) -> str:
        """Allocate the next global sequential recording stem."""
        self.settings.ensure_directories()
        max_index = 0
        for path in self.settings.recordings_dir.glob("recording-*.mp3"):
            match = re.fullmatch(r"recording-(\d+)\.mp3", path.name)
            if match is None:
                continue
            max_index = max(max_index, int(match.group(1)))
        return f"recording-{max_index + 1:04d}"

    def recording_stem(self, filename: str) -> str:
        """Return the filename stem used for sidecar and report artifacts."""
        return Path(filename).stem

    def metadata_path_for(self, filename: str) -> Path:
        """Return the metadata sidecar path for a recording filename."""
        return self.settings.recordings_dir / f"{self.recording_stem(filename)}.json"

    def report_path_for(self, filename: str) -> Path:
        """Return the markdown report path for a recording filename."""
        return self.settings.recordings_dir / f"{self.recording_stem(filename)}.md"

    def report_filename_for(self, filename: str) -> str:
        """Return the markdown report filename for a recording filename."""
        return self.report_path_for(filename).name

    def deep_analysis_path_for(self, filename: str) -> Path:
        """Return the deep-analysis markdown path for a recording filename."""
        return self.settings.recordings_dir / f"{self.recording_stem(filename)}.deep-analysis.md"

    def deep_analysis_filename_for(self, filename: str) -> str:
        """Return the deep-analysis markdown filename for a recording filename."""
        return self.deep_analysis_path_for(filename).name

    async def download_recording(self, media_url: str) -> bytes:
        """Download a recording from Twilio with basic auth."""
        try:
            response = await self.http_client.get(
                media_url,
                auth=(self.settings.twilio_account_sid, self.settings.twilio_auth_token),
            )
            response.raise_for_status()
            self.logger.info("recording_download_succeeded", media_url=media_url, bytes=len(response.content))
            return response.content
        except Exception as exc:
            self.logger.exception("recording_download_failed", media_url=media_url)
            raise RecordingManagerError("Failed to download recording.") from exc

    def save_recording(self, filename: str, payload: bytes) -> Path:
        """Persist recording bytes to disk."""
        self.settings.ensure_directories()
        destination = self.settings.recordings_dir / filename
        destination.write_bytes(payload)
        self.logger.info("recording_saved", path=str(destination), bytes=len(payload))
        return destination

    def save_metadata(self, filename: str, metadata: dict[str, Any]) -> Path:
        """Persist recording metadata as a JSON sidecar."""
        self.settings.ensure_directories()
        destination = self.metadata_path_for(filename)
        destination.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        self.logger.info("recording_metadata_saved", path=str(destination))
        return destination

    async def download_and_store(
        self,
        call_sid: str,
        recording_sid: str,
        media_url: str,
        metadata: CallMetadata | None = None,
        extension: str = "mp3",
    ) -> Path:
        """Download a recording, save it locally, and persist sidecar metadata."""
        filename = self.build_recording_filename(call_sid, recording_sid, extension)
        payload = await self.download_recording(media_url)
        recording_path = self.save_recording(filename, payload)
        display_name = self.recording_stem(filename)
        sidecar = {
            "call_sid": call_sid,
            "recording_sid": recording_sid,
            "media_url": media_url,
            "saved_at": datetime.now(UTC).isoformat(),
            "filename": filename,
            "display_name": display_name,
            "report_filename": self.report_filename_for(filename),
            "deep_analysis_filename": self.deep_analysis_filename_for(filename),
            "recording_path": str(recording_path),
        }
        if metadata is not None:
            metadata.recording_sid = recording_sid
            metadata.recording_path = str(recording_path)
            metadata.touch()
            sidecar["call_metadata"] = metadata.model_dump(mode="json")
        self.save_metadata(filename, sidecar)
        return recording_path

    async def close(self) -> None:
        """Close owned HTTP resources."""
        try:
            await asyncio.wait_for(self.http_client.aclose(), timeout=5.0)
        except TimeoutError:
            self.logger.warning("recording_http_client_close_timed_out")

    def list_recordings(self, limit: int = 50) -> list[RecordingSummary]:
        """Return stored recordings ordered from newest to oldest."""
        self.settings.ensure_directories()
        summaries: list[RecordingSummary] = []
        for sidecar_path in self.settings.recordings_dir.glob("*.json"):
            try:
                payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
                recording_path = str(payload.get("recording_path") or "").strip()
                if not recording_path:
                    continue
                summaries.append(
                    RecordingSummary(
                        call_sid=str(payload.get("call_sid") or ""),
                        recording_sid=str(payload.get("recording_sid") or ""),
                        saved_at=str(payload.get("saved_at") or ""),
                        filename=str(payload.get("filename") or Path(recording_path).name),
                        display_name=str(payload.get("display_name") or Path(recording_path).stem),
                        media_url=str(payload.get("media_url") or "") or None,
                        report_filename=self.report_filename_for(str(payload.get("filename") or Path(recording_path).name)),
                        report_available=self.report_path_for(
                            str(payload.get("filename") or Path(recording_path).name)
                        ).is_file(),
                        deep_analysis_filename=self.deep_analysis_filename_for(
                            str(payload.get("filename") or Path(recording_path).name)
                        ),
                        deep_analysis_available=self.deep_analysis_path_for(
                            str(payload.get("filename") or Path(recording_path).name)
                        ).is_file(),
                        recording_path=recording_path,
                    )
                )
            except (OSError, json.JSONDecodeError, ValueError):
                self.logger.warning("recording_sidecar_parse_failed", path=str(sidecar_path))
        summaries.sort(key=lambda item: item.saved_at, reverse=True)
        return summaries[:limit]

    def resolve_recording_path(self, filename: str) -> Path:
        """Resolve a dashboard recording filename inside the recordings directory."""
        if not filename or filename in {".", ".."} or "/" in filename:
            raise RecordingManagerError("Invalid recording filename.")
        candidate = (self.settings.recordings_dir / filename).resolve()
        recordings_root = self.settings.recordings_dir.resolve()
        if recordings_root not in candidate.parents or not candidate.is_file():
            raise RecordingManagerError("Recording not found.")
        return candidate

    def resolve_report_path(self, filename: str) -> Path:
        """Resolve the markdown report associated with a dashboard recording filename."""
        if not filename or filename in {".", ".."} or "/" in filename:
            raise RecordingManagerError("Invalid recording filename.")
        candidate = self.report_path_for(filename).resolve()
        recordings_root = self.settings.recordings_dir.resolve()
        if recordings_root not in candidate.parents or not candidate.is_file():
            raise RecordingManagerError("Recording report not found.")
        return candidate

    def resolve_deep_analysis_path(self, filename: str) -> Path:
        """Resolve the deep-analysis markdown associated with a dashboard recording filename."""
        if not filename or filename in {".", ".."} or "/" in filename:
            raise RecordingManagerError("Invalid recording filename.")
        candidate = self.deep_analysis_path_for(filename).resolve()
        recordings_root = self.settings.recordings_dir.resolve()
        if recordings_root not in candidate.parents or not candidate.is_file():
            raise RecordingManagerError("Recording deep analysis not found.")
        return candidate
