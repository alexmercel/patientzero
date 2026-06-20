from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.telephony.recording_manager import RecordingManager


class FakeAsyncClient:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.requested_urls: list[str] = []
        self.closed = False

    async def get(self, url: str, auth: tuple[str, str]):
        self.requested_urls.append(url)
        return SimpleNamespace(content=self.payload, raise_for_status=lambda: None)

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_recording_manager_downloads_and_saves(settings) -> None:
    fake_http = FakeAsyncClient(payload=b"audio-data")
    manager = RecordingManager(settings, http_client=fake_http)
    metadata = CallMetadata(
        call_sid="CA123",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
    )

    saved_path = await manager.download_and_store(
        call_sid="CA123",
        recording_sid="RE123",
        media_url="https://api.twilio.test/recording.mp3",
        metadata=metadata,
    )

    assert saved_path.exists()
    assert saved_path.name == "recording-0001.mp3"
    assert manager.metadata_path_for(saved_path.name).exists()
    assert metadata.recording_path == str(saved_path)

    await manager.close()
    assert fake_http.closed is True


@pytest.mark.asyncio
async def test_recording_manager_lists_saved_recordings(settings) -> None:
    fake_http = FakeAsyncClient(payload=b"audio-data")
    manager = RecordingManager(settings, http_client=fake_http)

    await manager.download_and_store(
        call_sid="CA123",
        recording_sid="RE123",
        media_url="https://api.twilio.test/recording.mp3",
    )

    recordings = manager.list_recordings()

    assert recordings[0].recording_sid == "RE123"
    assert recordings[0].filename == "recording-0001.mp3"
    assert recordings[0].display_name == "recording-0001"
    assert recordings[0].deep_analysis_filename == "recording-0001.deep-analysis.md"
    assert recordings[0].deep_analysis_available is False

    await manager.close()


@pytest.mark.asyncio
async def test_recording_manager_allocates_next_sequential_filename(settings) -> None:
    fake_http = FakeAsyncClient(payload=b"audio-data")
    manager = RecordingManager(settings, http_client=fake_http)
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    (settings.recordings_dir / "recording-0007.mp3").write_bytes(b"older-audio")

    saved_path = await manager.download_and_store(
        call_sid="CA124",
        recording_sid="RE124",
        media_url="https://api.twilio.test/recording-2.mp3",
    )

    assert saved_path.name == "recording-0008.mp3"

    await manager.close()


@pytest.mark.asyncio
async def test_recording_manager_lists_report_metadata(settings) -> None:
    fake_http = FakeAsyncClient(payload=b"audio-data")
    manager = RecordingManager(settings, http_client=fake_http)

    await manager.download_and_store(
        call_sid="CA123",
        recording_sid="RE123",
        media_url="https://api.twilio.test/recording.mp3",
    )
    report_path = manager.report_path_for("recording-0001.mp3")
    report_path.write_text("# report\n", encoding="utf-8")

    recordings = manager.list_recordings()

    assert recordings[0].report_available is True
    assert recordings[0].report_filename == "recording-0001.md"
    assert recordings[0].deep_analysis_available is False

    await manager.close()


@pytest.mark.asyncio
async def test_recording_manager_lists_deep_analysis_metadata(settings) -> None:
    fake_http = FakeAsyncClient(payload=b"audio-data")
    manager = RecordingManager(settings, http_client=fake_http)

    await manager.download_and_store(
        call_sid="CA123",
        recording_sid="RE123",
        media_url="https://api.twilio.test/recording.mp3",
    )
    analysis_path = manager.deep_analysis_path_for("recording-0001.mp3")
    analysis_path.write_text("# deep analysis\n", encoding="utf-8")

    recordings = manager.list_recordings()

    assert recordings[0].deep_analysis_available is True
    assert recordings[0].deep_analysis_filename == "recording-0001.deep-analysis.md"

    await manager.close()
