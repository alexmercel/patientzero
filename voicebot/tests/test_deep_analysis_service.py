from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from voicebot.src.testing.campaign_manager import (
    RecordingReference,
    ScenarioEvaluation,
    TestRunRecord as CampaignRunRecord,
)
from voicebot.src.testing.deep_analysis_service import DeepAnalysisService


class FakeGenerateResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeModels:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_first_call = False

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_first_call and len(self.calls) == 1:
            raise RuntimeError("quota exhausted")
        return FakeGenerateResponse("## Overall Verdict\nDeep analysis result.")


class FakeClient:
    def __init__(self) -> None:
        self.models = FakeModels()


@pytest.mark.asyncio
async def test_deep_analysis_service_writes_markdown_from_audio(settings) -> None:
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    recording_path = settings.recordings_dir / "recording-0001.mp3"
    recording_path.write_bytes(b"fake-audio")
    client = FakeClient()
    service = DeepAnalysisService(settings, client=client)

    run = CampaignRunRecord(
        run_id="run_123",
        call_sid="CA123",
        persona_id="PAT01",
        persona_name="Priya Desai",
        started_at="2026-06-20T00:00:00Z",
        completed_at="2026-06-20T00:05:00Z",
        status="completed",
        scenario_ids=["SCH01"],
        scenario_results=[
            ScenarioEvaluation(
                scenario_id="SCH01",
                category="scheduling",
                title="Weekend appointment",
                tested=True,
                outcome="fail",
                details="Representative did not offer a weekday alternative.",
                ask=["Can I come Sunday around 10 AM?"],
                expected="Offer weekday alternatives",
            )
        ],
        recordings=[
            RecordingReference(
                call_sid="CA123",
                recording_sid="RE123",
                saved_at="2026-06-20T00:05:30Z",
                filename="recording-0001.mp3",
                display_name="recording-0001",
                recording_path=str(recording_path),
            )
        ],
    )

    service.schedule_run_analysis(run)
    await service.close()

    output_path = settings.recordings_dir / "recording-0001.deep-analysis.md"
    assert output_path.is_file()
    assert "Deep Analysis - recording-0001" in output_path.read_text(encoding="utf-8")
    assert client.models.calls
    first_call = client.models.calls[0]
    assert first_call["model"] == settings.gemini_deep_analysis_model
    prompt = first_call["contents"][0]
    assert "You are a senior QA engineer reviewing a production healthcare voice agent." in prompt
    assert "Listen directly to the attached audio file." in prompt
    assert "- hallucinations" in prompt
    assert "- identity leakage" in prompt
    assert "- contradictory responses" in prompt
    assert "- timestamp" in prompt
    assert "- severity" in prompt
    assert "- confidence" in prompt
    assert "Be conservative and do not invent bugs." in prompt


@pytest.mark.asyncio
async def test_deep_analysis_service_trims_first_three_seconds_before_analysis(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    recording_path = settings.recordings_dir / "recording-0001.mp3"
    recording_path.write_bytes(b"original-audio")
    client = FakeClient()
    service = DeepAnalysisService(settings, client=client)

    monkeypatch.setattr(
        "voicebot.src.testing.deep_analysis_service.shutil.which",
        lambda name: "/usr/local/bin/ffmpeg" if name == "ffmpeg" else None,
    )

    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout=b"trimmed-audio", stderr=b"")

    monkeypatch.setattr("voicebot.src.testing.deep_analysis_service.subprocess.run", fake_run)

    class FakePartFactory:
        @staticmethod
        def from_bytes(*, data: bytes, mime_type: str):
            return {"data": data, "mime_type": mime_type}

    monkeypatch.setattr(
        "voicebot.src.testing.deep_analysis_service.genai_types",
        SimpleNamespace(Part=FakePartFactory),
    )

    run = CampaignRunRecord(
        run_id="run_123",
        call_sid="CA123",
        persona_id="PAT01",
        persona_name="Priya Desai",
        started_at="2026-06-20T00:00:00Z",
        completed_at="2026-06-20T00:05:00Z",
        status="completed",
        scenario_ids=["SCH01"],
        scenario_results=[],
        recordings=[
            RecordingReference(
                call_sid="CA123",
                recording_sid="RE123",
                saved_at="2026-06-20T00:05:30Z",
                filename="recording-0001.mp3",
                display_name="recording-0001",
                recording_path=str(recording_path),
            )
        ],
    )

    service.schedule_run_analysis(run)
    await service.close()

    first_call = client.models.calls[0]
    assert first_call["contents"][1]["data"] == b"trimmed-audio"
    assert first_call["contents"][1]["mime_type"] == "audio/mpeg"


@pytest.mark.asyncio
async def test_deep_analysis_service_falls_back_to_flash_lite(settings) -> None:
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    recording_path = settings.recordings_dir / "recording-0001.mp3"
    recording_path.write_bytes(b"fake-audio")
    client = FakeClient()
    client.models.fail_first_call = True
    service = DeepAnalysisService(settings, client=client)

    run = CampaignRunRecord(
        run_id="run_123",
        call_sid="CA123",
        persona_id="PAT01",
        persona_name="Priya Desai",
        started_at="2026-06-20T00:00:00Z",
        completed_at="2026-06-20T00:05:00Z",
        status="completed",
        scenario_ids=["SCH01"],
        scenario_results=[
            ScenarioEvaluation(
                scenario_id="SCH01",
                category="scheduling",
                title="Weekend appointment",
                tested=True,
                outcome="fail",
                details="Representative did not offer a weekday alternative.",
                ask=["Can I come Sunday around 10 AM?"],
                expected="Offer weekday alternatives",
            )
        ],
        recordings=[
            RecordingReference(
                call_sid="CA123",
                recording_sid="RE123",
                saved_at="2026-06-20T00:05:30Z",
                filename="recording-0001.mp3",
                display_name="recording-0001",
                recording_path=str(recording_path),
            )
        ],
    )

    service.schedule_run_analysis(run)
    await service.close()

    assert [call["model"] for call in client.models.calls] == [
        settings.gemini_deep_analysis_model,
        settings.gemini_deep_analysis_fallback_model,
    ]
    output_path = settings.recordings_dir / "recording-0001.deep-analysis.md"
    assert f"- Model used: {settings.gemini_deep_analysis_fallback_model}" in output_path.read_text(encoding="utf-8")
