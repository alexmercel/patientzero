"""Scenario-driven patient test campaign planning and reporting."""

from __future__ import annotations

import json
import random
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from pydantic import BaseModel, Field

from voicebot.src.config.settings import Settings
from voicebot.src.models.api_models import RecordingSummary
from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.models.session_state import SessionState, TranscriptTurn
from voicebot.src.observability.event_bus import DashboardEventBus
from voicebot.src.utils.logging import get_logger


class TestPersona(BaseModel):
    """Structured patient persona used for test calls."""

    persona_id: str
    full_name: str
    age: int
    address: str
    tone: str
    speaking_style: str
    background: str
    insurance: str
    medical_context: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)


class TestScenario(BaseModel):
    """One scripted scenario that the patient should try during a call."""

    scenario_id: str
    category: str
    title: str
    ask: list[str] = Field(default_factory=list)
    expected: str | None = None


class TranscriptEvidenceTurn(BaseModel):
    """One exact transcript turn captured for reporting and evidence."""

    turn_index: int
    speaker: str
    text: str
    timestamp: str


class RecordingReference(BaseModel):
    """One dashboard-facing recording attached to a test run."""

    call_sid: str
    recording_sid: str
    saved_at: str
    filename: str
    display_name: str | None = None
    media_url: str | None = None
    report_filename: str | None = None
    report_available: bool = False
    deep_analysis_filename: str | None = None
    deep_analysis_available: bool = False
    recording_path: str | None = None


class LiveScenarioState(BaseModel):
    """Current scenario progression for an active test run."""

    scenario_id: str
    category: str
    title: str
    sequence: int
    total: int
    status: str
    ask: list[str] = Field(default_factory=list)
    matched_ask_lines: int = 0
    representative_turns_observed: int = 0


class ScenarioPlanItem(BaseModel):
    """One upcoming planned scenario for preview and reporting UIs."""

    scenario_id: str
    category: str
    title: str
    ask: list[str] = Field(default_factory=list)
    expected: str | None = None


class ScenarioCoverageRecord(BaseModel):
    """Persisted outcome state for one scenario across many runs."""

    scenario_id: str
    category: str
    title: str
    tested: bool = False
    outcome: str = "pending"
    details: str = "Not tested yet."
    evidence: str | None = None
    expected: str | None = None
    ask: list[str] = Field(default_factory=list)
    last_run_id: str | None = None
    last_persona_id: str | None = None
    last_tested_at: str | None = None
    run_count: int = 0
    pass_count: int = 0
    fail_count: int = 0


class ScenarioEvaluation(BaseModel):
    """One scenario outcome within a single test run."""

    scenario_id: str
    category: str
    title: str
    tested: bool
    outcome: str
    details: str
    evidence: str | None = None
    ask: list[str] = Field(default_factory=list)
    expected: str | None = None
    ask_turns: list[TranscriptEvidenceTurn] = Field(default_factory=list)
    response_turns: list[TranscriptEvidenceTurn] = Field(default_factory=list)


class TestRunRecord(BaseModel):
    """Complete record for one phone call campaign run."""

    run_id: str
    call_sid: str | None = None
    persona_id: str
    persona_name: str
    started_at: str
    completed_at: str | None = None
    status: str = "planned"
    scenario_ids: list[str] = Field(default_factory=list)
    scenario_asks: dict[str, list[str]] = Field(default_factory=dict)
    scenario_plan: list[ScenarioPlanItem] = Field(default_factory=list)
    current_scenario: LiveScenarioState | None = None
    scenario_results: list[ScenarioEvaluation] = Field(default_factory=list)
    transcript_turns: list[TranscriptEvidenceTurn] = Field(default_factory=list)
    transcript_excerpt: str | None = None
    recordings: list[RecordingReference] = Field(default_factory=list)
    active_scenario_index: int = Field(default=0, exclude=True)
    active_matched_ask_lines: int = Field(default=0, exclude=True)
    active_representative_turns: int = Field(default=0, exclude=True)
    active_status: str = Field(default="queued", exclude=True)
    last_processed_turn_count: int = Field(default=0, exclude=True)
    last_representative_signature: str | None = Field(default=None, exclude=True)
    repeated_representative_count: int = Field(default=0, exclude=True)
    active_response_segments: list[str] = Field(default_factory=list, exclude=True)

    @property
    def tested_count(self) -> int:
        return sum(1 for item in self.scenario_results if item.tested)

    @property
    def pass_count(self) -> int:
        return sum(1 for item in self.scenario_results if item.outcome == "pass")

    @property
    def fail_count(self) -> int:
        return sum(1 for item in self.scenario_results if item.outcome == "fail")


class TestCampaignReport(BaseModel):
    """Persisted cross-run campaign report."""

    generated_at: str
    scenario_results: list[ScenarioCoverageRecord] = Field(default_factory=list)
    recent_runs: list[TestRunRecord] = Field(default_factory=list)


class TestCampaignManager:
    """Chooses personas and scenarios, then evaluates completed calls."""

    __test__ = False

    def __init__(
        self,
        settings: Settings,
        event_bus: DashboardEventBus | None = None,
        rng: random.Random | None = None,
        deep_analysis_service: Any | None = None,
    ) -> None:
        self.settings = settings
        self.event_bus = event_bus
        self.logger = get_logger(__name__)
        self._rng = rng or random.Random()
        self._deep_analysis_service = deep_analysis_service
        self._personas = _load_personas(settings.test_personas_path)
        self._scenarios = _load_scenarios(settings.test_scenarios_path)
        self._scenario_map = {scenario.scenario_id: scenario for scenario in self._scenarios}
        self._planned_runs: dict[str, TestRunRecord] = {}
        self._active_runs: dict[str, TestRunRecord] = {}
        self._next_run_preview_id: str | None = None
        self._report = self._load_report()

    def preview_next_call(self) -> TestRunRecord:
        """Return a cached preview of the next persona/scenario bundle."""
        if self._next_run_preview_id is not None:
            preview = self._planned_runs.get(self._next_run_preview_id)
            if preview is not None:
                return preview
            self._next_run_preview_id = None

        run = self._create_planned_run()
        self._planned_runs[run.run_id] = run
        self._next_run_preview_id = run.run_id
        return run

    def plan_next_call(self) -> tuple[TestRunRecord, dict[str, str]]:
        """Choose a persona and a batch of scenarios for a new test call."""
        run = self.preview_next_call()
        self._next_run_preview_id = None
        return run, self._custom_parameters_for_run(run)

    def _create_planned_run(self) -> TestRunRecord:
        """Build a planned run without activating a Twilio call."""
        persona = self._rng.choice(self._personas)
        scenarios = self._select_scenarios_for_call()
        scenario_asks = {
            scenario.scenario_id: _render_scenario_ask_lines(scenario, persona, self.settings)
            for scenario in scenarios
        }
        run_id = f"run_{uuid4().hex[:12]}"
        run = TestRunRecord(
            run_id=run_id,
            persona_id=persona.persona_id,
            persona_name=persona.full_name,
            started_at=_utcnow(),
            status="planned",
            scenario_ids=[scenario.scenario_id for scenario in scenarios],
            scenario_asks=scenario_asks,
            scenario_plan=[
                ScenarioPlanItem(
                    scenario_id=scenario.scenario_id,
                    category=scenario.category,
                    title=scenario.title,
                    ask=list(scenario_asks.get(scenario.scenario_id, scenario.ask)),
                    expected=scenario.expected,
                )
                for scenario in scenarios
            ],
        )
        run.current_scenario = self._current_live_state_from_run(run)
        return run

    def _custom_parameters_for_run(self, run: TestRunRecord) -> dict[str, str]:
        return {
            "test_mode": "true",
            "test_run_id": run.run_id,
            "test_persona_id": run.persona_id,
            "test_scenario_ids": ",".join(run.scenario_ids),
        }

    def _invalidate_preview(self) -> None:
        if self._next_run_preview_id is not None:
            self._planned_runs.pop(self._next_run_preview_id, None)
            self._next_run_preview_id = None

    def activate_run(self, metadata: CallMetadata) -> TestRunRecord | None:
        """Attach the returned Twilio call SID to a planned run."""
        run_id = str(metadata.custom_parameters.get("test_run_id") or "").strip()
        if not run_id:
            return None

        run = self._planned_runs.pop(run_id, None)
        if run is None:
            return None

        run.call_sid = metadata.call_sid
        run.status = "active"
        self._active_runs[metadata.call_sid] = run
        run.current_scenario = self._current_live_state_from_run(run)
        self._publish_state_event(
            "testing_run_started",
            {
                "run_id": run.run_id,
                "call_sid": metadata.call_sid,
                "persona_id": run.persona_id,
                "scenario_ids": run.scenario_ids,
                "current_scenario": (
                    run.current_scenario.model_dump(mode="json")
                    if run.current_scenario is not None
                    else None
                ),
            },
        )
        return run

    def build_call_instruction(self, call_sid: str) -> str | None:
        """Return persona context and the current scenario only."""
        run = self._active_runs.get(call_sid)
        if run is None:
            return None

        persona = self._persona_by_id(run.persona_id)
        current_scenario = self._active_scenario_for_run(run)
        lines = [
            "Hidden test-call instructions for this call only:",
            "Stay fully in character as the patient persona below.",
            "Do not say you are testing, following a script, or running scenarios.",
            f"Persona name: {persona.full_name}",
            f"Persona age: {persona.age}",
            f"Persona address: {self.settings.testing_demo_address}",
            f"Persona tone: {persona.tone}",
            f"Persona speaking style: {persona.speaking_style}",
            f"Persona background: {persona.background}",
            f"Insurance detail to use if asked: {persona.insurance}",
        ]
        if persona.medical_context:
            lines.append("Relevant medical context:")
            for item in persona.medical_context:
                lines.append(f"- {item}")
        if persona.preferences:
            lines.append("Behavior preferences:")
            for item in persona.preferences:
                lines.append(f"- {item}")
        lines.extend(
            [
                "Conversation strategy:",
                "- Start the call like a real patient. Greet naturally, let the representative lead intake, and wait to share your name until asked.",
                "- If the representative asks for your name, provide your persona name and allow them to create or verify the profile before moving into the request.",
                "- Bring up only the current issue you are working on. Do not mention future issues unless you receive a separate hidden transition note later in the call.",
                "- Use wording close to the provided ask lines so the evaluation can recognize what you tested, but keep the conversation natural.",
                "- If the first ask line is a correction, contradiction, or short fragment, first open the topic naturally before using that line in context.",
                "- For multi-line scenarios, use the later lines only as the conversation develops; do not dump every line at once.",
                "- Stay on the current scenario until the representative has clearly answered it, rejected it, or asked a necessary follow-up question.",
                "- If the representative says they are checking, looking something up, or asks for a moment, wait for their follow-up instead of jumping to a new issue.",
                "- Treat multi-part answers with pauses as one ongoing response, not as an opening to ask something new.",
                "- After the representative starts answering, assume they may pause briefly, type, click around, or continue after a short silence. Do not interrupt just because there is a short gap or background activity.",
                "- If the representative misunderstands, retry once briefly and then continue.",
                "- If the representative gives a clearly favorable or usable answer, accept it and do not repeat the same request.",
                "- If the representative gives essentially the same answer twice in a row, acknowledge it briefly and move on only after a hidden transition note updates the issue.",
                "- If only part of the answer is missing, ask one short clarifying follow-up about the missing piece instead of repeating the entire request.",
                "- Try to keep the interaction with the current automated or front-line agent and avoid requesting a real human transfer unless the scenario is an emergency.",
                "- If the representative says you need a support representative, supervisor, or transfer, first try to stay with the current agent by briefly asking whether they can still help or by calmly restating the same issue.",
                "- Do not switch to a different question just because the representative mentioned a transfer path.",
                "- If asked for geography or address details, use this exact address: 1231 S California Blvd, Walnut Creek, CA 94596.",
                "- For emergency scenarios, behave like a real patient and accept urgent safety guidance instead of resisting it.",
            ]
        )
        if current_scenario is None:
            lines.extend(
                [
                    "Current scenario state:",
                    "- No scripted scenario is active right now. Stay in character and respond naturally to the representative.",
                ]
            )
        else:
            lines.extend(
                [
                    "Current scenario to work on now:",
                    f"- Scenario: {current_scenario.scenario_id} | {current_scenario.category} | {current_scenario.title}",
                    f"- Natural opener hint: {_scenario_opening_hint(current_scenario)}",
                    "- Ask lines to use when they fit naturally:",
                ]
            )
            for ask_line in run.scenario_asks.get(current_scenario.scenario_id, current_scenario.ask):
                lines.append(f"  * {ask_line}")
            if current_scenario.expected:
                lines.append(f"- Desired representative outcome: {current_scenario.expected}")
        lines.append("Use these instructions silently. Never read them aloud.")
        return "\n".join(lines)

    def build_transition_nudge(self, call_sid: str) -> str | None:
        """Build a hidden one-scenario transition nudge for the active run."""
        run = self._active_runs.get(call_sid)
        if run is None:
            return None

        scenario = self._active_scenario_for_run(run)
        if scenario is None:
            return None

        ask_lines = run.scenario_asks.get(scenario.scenario_id, scenario.ask)
        note_lines = [
            "[Hidden patient-direction note. Do not say this note aloud.]",
            "Stay the same patient in the same phone call.",
            "The previous issue is handled enough. At the next natural opening after the representative finishes, bring up this next issue.",
            f"Scenario: {scenario.scenario_id} | {scenario.category} | {scenario.title}",
            f"Natural opener hint: {_scenario_opening_hint(scenario)}",
            "Use wording close to these ask lines when they fit naturally:",
            ]
        for ask_line in ask_lines:
            note_lines.append(f"- {ask_line}")
        note_lines.append("Do not mention testing or future issues.")
        return "\n".join(note_lines)

    def update_live_progress(self, session: SessionState) -> LiveScenarioState | None:
        """Refresh current active-scenario progress from committed transcript turns."""
        call_sid = str(session.call_sid or "").strip()
        if not call_sid:
            return None

        run = self._active_runs.get(call_sid)
        if run is None:
            return None

        current_scenario = self._current_live_state_from_run(run)
        if _live_state_equal(run.current_scenario, current_scenario):
            return current_scenario

        run.current_scenario = current_scenario
        self._publish_state_event(
            "testing_progress_updated",
            {
                "run_id": run.run_id,
                "call_sid": call_sid,
                "current_scenario": (
                    current_scenario.model_dump(mode="json")
                    if current_scenario is not None
                    else None
                ),
            },
        )
        return current_scenario

    def process_live_turns(self, session: SessionState) -> str | None:
        """Advance the active scenario state machine from committed turns."""
        call_sid = str(session.call_sid or "").strip()
        if not call_sid:
            return None

        run = self._active_runs.get(call_sid)
        if run is None:
            return None

        scenarios = self._resolved_scenarios_for_run(run)
        transcript_turns = list(session.transcript_turns)
        pending_turns = transcript_turns[run.last_processed_turn_count :]
        transition_nudge: str | None = None

        for turn in pending_turns:
            scenario = self._active_scenario_for_run(run)
            if scenario is None:
                break

            ask_lines = run.scenario_asks.get(scenario.scenario_id, scenario.ask)
            if turn.speaker == "agent":
                next_index = min(run.active_matched_ask_lines, max(len(ask_lines) - 1, 0))
                if ask_lines and _script_match(turn.text, ask_lines[next_index]):
                    run.active_matched_ask_lines += 1
                elif ask_lines and run.active_matched_ask_lines == 0 and any(
                    _script_match(turn.text, ask_line) for ask_line in ask_lines
                ):
                    run.active_matched_ask_lines = 1

                if run.active_matched_ask_lines >= len(ask_lines) and ask_lines:
                    run.active_status = "awaiting_response"
                elif run.active_matched_ask_lines > 0:
                    run.active_status = "asking"
                continue

            if run.active_matched_ask_lines < len(ask_lines):
                continue

            if _is_stall_response(turn.text):
                run.active_status = "awaiting_response"
                continue

            if _is_follow_up_question(turn.text):
                run.active_representative_turns += 1
                run.active_status = "awaiting_response"
                run.active_response_segments.append(turn.text)
                continue

            run.active_representative_turns += 1
            run.active_status = "response_received"
            run.active_response_segments.append(turn.text)

            normalized = _normalize_text(turn.text)
            if normalized and _turns_are_equivalent(run.last_representative_signature, normalized):
                run.repeated_representative_count += 1
            else:
                run.last_representative_signature = normalized or None
                run.repeated_representative_count = 1 if normalized else 0

            cumulative_response = " ".join(run.active_response_segments).strip()
            _, passed = _evaluate_representative_response(scenario, cumulative_response)
            if passed or run.repeated_representative_count >= 2:
                if self._advance_run_to_next_scenario(run, scenarios):
                    transition_nudge = self.build_transition_nudge(call_sid)
                else:
                    run.active_status = "completed"

        run.last_processed_turn_count = len(transcript_turns)
        self.update_live_progress(session)
        return transition_nudge

    def finalize_call(self, session: SessionState) -> TestRunRecord | None:
        """Evaluate the transcript from a completed call and update the report."""
        call_sid = str(session.call_sid or "").strip()
        if not call_sid:
            return None

        run = self._active_runs.pop(call_sid, None)
        if run is None:
            return None

        scenarios = self._resolved_scenarios_for_run(run)
        transcript_turns = list(session.transcript_turns)
        results = _evaluate_scenarios(scenarios, transcript_turns)

        run.status = "completed"
        run.completed_at = _utcnow()
        run.current_scenario = _build_live_scenario_state(scenarios, transcript_turns)
        run.scenario_results = results
        run.transcript_turns = _build_transcript_turn_records(transcript_turns)
        run.transcript_excerpt = _build_transcript_excerpt(transcript_turns)
        run.recordings = _recordings_from_session(session)

        self._merge_run_into_report(run)
        self._write_markdown_reports(run)
        self._schedule_deep_analysis(run)
        self._persist_report()
        self._invalidate_preview()
        self._publish_state_event(
            "testing_report_updated",
            {
                "run_id": run.run_id,
                "call_sid": call_sid,
                "tested_count": run.tested_count,
                "pass_count": run.pass_count,
                "fail_count": run.fail_count,
            },
        )
        return run

    def reset_campaign(self) -> None:
        """Reset aggregate scenario coverage while preserving historical run evidence."""
        self._planned_runs.clear()
        self._next_run_preview_id = None
        self._report.scenario_results = self._empty_scenario_records()
        self._report.generated_at = _utcnow()
        self._persist_report()
        self._publish_state_event(
            "testing_campaign_reset",
            {
                "generated_at": self._report.generated_at,
                "recent_run_count": len(self._report.recent_runs),
            },
        )

    def sync_recordings(
        self,
        recordings: list[RecordingSummary] | list[dict[str, Any]],
        *,
        persist: bool = True,
        write_reports: bool = True,
    ) -> None:
        """Attach the latest recording summaries to matching runs by call SID."""
        grouped: dict[str, list[RecordingReference]] = defaultdict(list)
        for recording in recordings:
            reference = _coerce_recording_reference(recording)
            if not reference.call_sid:
                continue
            grouped[reference.call_sid].append(reference)

        for references in grouped.values():
            references.sort(key=lambda item: (item.saved_at, item.filename), reverse=True)

        persisted_change = False
        report_refresh_runs: list[TestRunRecord] = []
        for run in self._report.recent_runs:
            references = list(grouped.get(str(run.call_sid or "").strip(), []))
            if _recordings_equal(run.recordings, references):
                continue
            run.recordings = references
            persisted_change = True
            report_refresh_runs.append(run)

        for run in self._active_runs.values():
            references = list(grouped.get(str(run.call_sid or "").strip(), []))
            if _recordings_equal(run.recordings, references):
                continue
            run.recordings = references
            if run.status == "completed":
                report_refresh_runs.append(run)

        if write_reports:
            for run in report_refresh_runs:
                self._write_markdown_reports(run)
                self._schedule_deep_analysis(run)

        if persist and persisted_change:
            self._persist_report()

    def report_snapshot(
        self,
        recordings: list[RecordingSummary] | list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Return a dashboard-facing snapshot of testing coverage."""
        if recordings is not None:
            self.sync_recordings(recordings, persist=False, write_reports=False)
        scenario_results = [record.model_dump(mode="json") for record in self._report.scenario_results]
        recent_runs = [run.model_dump(mode="json") for run in self._report.recent_runs[:20]]
        tested_count = sum(1 for item in self._report.scenario_results if item.tested)
        passed_count = sum(1 for item in self._report.scenario_results if item.outcome == "pass")
        failed_count = sum(1 for item in self._report.scenario_results if item.outcome == "fail")
        pending_count = len(self._report.scenario_results) - tested_count
        active_runs = [
            run.model_dump(mode="json")
            for run in self._active_runs.values()
        ]
        next_run_preview = self.preview_next_call().model_dump(mode="json")
        return {
            "generated_at": self._report.generated_at,
            "summary": {
                "total_scenarios": len(self._report.scenario_results),
                "tested_count": tested_count,
                "passed_count": passed_count,
                "failed_count": failed_count,
                "pending_count": pending_count,
                "recent_run_count": len(self._report.recent_runs),
            },
            "scenario_results": scenario_results,
            "recent_runs": recent_runs,
            "active_runs": active_runs,
            "next_run_preview": next_run_preview,
        }

    def _active_scenario_for_run(self, run: TestRunRecord) -> TestScenario | None:
        scenarios = self._resolved_scenarios_for_run(run)
        if not scenarios:
            return None
        if run.active_scenario_index >= len(scenarios):
            return None
        return scenarios[run.active_scenario_index]

    def _current_live_state_from_run(self, run: TestRunRecord) -> LiveScenarioState | None:
        scenario = self._active_scenario_for_run(run)
        if scenario is None:
            return None
        return LiveScenarioState(
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            title=scenario.title,
            sequence=min(run.active_scenario_index + 1, len(run.scenario_ids)),
            total=len(run.scenario_ids),
            status=run.active_status,
            ask=list(run.scenario_asks.get(scenario.scenario_id, scenario.ask)),
            matched_ask_lines=run.active_matched_ask_lines,
            representative_turns_observed=run.active_representative_turns,
        )

    def _advance_run_to_next_scenario(self, run: TestRunRecord, scenarios: list[TestScenario]) -> bool:
        next_index = run.active_scenario_index + 1
        if next_index >= len(scenarios):
            return False
        run.active_scenario_index = next_index
        run.active_matched_ask_lines = 0
        run.active_representative_turns = 0
        run.active_status = "queued"
        run.last_representative_signature = None
        run.repeated_representative_count = 0
        run.active_response_segments = []
        return True

    def _resolved_scenarios_for_run(self, run: TestRunRecord) -> list[TestScenario]:
        return [
            self._scenario_map[scenario_id].model_copy(
                update={"ask": run.scenario_asks.get(scenario_id, self._scenario_map[scenario_id].ask)}
            )
            for scenario_id in run.scenario_ids
            if scenario_id in self._scenario_map
        ]

    def _select_scenarios_for_call(self) -> list[TestScenario]:
        grouped: dict[str, list[TestScenario]] = defaultdict(list)
        report_map = {record.scenario_id: record for record in self._report.scenario_results}
        for scenario in self._scenarios:
            grouped[scenario.category].append(scenario)

        for category, scenarios in grouped.items():
            scenarios.sort(
                key=lambda item: _scenario_priority(report_map.get(item.scenario_id))
            )
            grouped[category] = scenarios

        category_order = sorted(
            grouped,
            key=lambda category: min(
                _scenario_priority(report_map.get(scenario.scenario_id))
                for scenario in grouped[category]
            ),
        )
        selected: list[TestScenario] = []
        selected_counts: dict[str, int] = defaultdict(int)
        max_total = self.settings.testing_max_scenarios_per_call
        max_per_category = self.settings.testing_max_scenarios_per_category

        while len(selected) < max_total:
            added_any = False
            for category in category_order:
                if len(selected) >= max_total:
                    break
                current_count = selected_counts[category]
                if current_count >= max_per_category:
                    continue
                candidates = grouped[category]
                if current_count >= len(candidates):
                    continue
                selected.append(candidates[current_count])
                selected_counts[category] += 1
                added_any = True
            if not added_any:
                break
        return selected

    def _load_report(self) -> TestCampaignReport:
        path = self.settings.test_report_path
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                report = TestCampaignReport.model_validate(payload)
            except (OSError, ValueError):
                self.logger.warning("testing_report_load_failed", path=str(path))
                report = TestCampaignReport(generated_at=_utcnow())
        else:
            report = TestCampaignReport(generated_at=_utcnow())

        report_map = {record.scenario_id: record for record in report.scenario_results}
        seeded_records: list[ScenarioCoverageRecord] = []
        for fresh_record in self._empty_scenario_records():
            previous = report_map.get(fresh_record.scenario_id)
            if previous is None:
                seeded_records.append(fresh_record)
                continue
            previous.category = fresh_record.category
            previous.title = fresh_record.title
            previous.expected = fresh_record.expected
            previous.ask = list(fresh_record.ask)
            seeded_records.append(previous)
        report.scenario_results = seeded_records
        report.generated_at = _utcnow()
        return report

    def _persist_report(self) -> None:
        self.settings.test_report_path.parent.mkdir(parents=True, exist_ok=True)
        self._report.generated_at = _utcnow()
        self.settings.test_report_path.write_text(
            json.dumps(self._report.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _merge_run_into_report(self, run: TestRunRecord) -> None:
        report_map = {record.scenario_id: record for record in self._report.scenario_results}
        for result in run.scenario_results:
            record = report_map[result.scenario_id]
            record.tested = record.tested or result.tested
            if result.tested:
                record.outcome = result.outcome
                record.details = result.details
                record.evidence = result.evidence
                record.last_run_id = run.run_id
                record.last_persona_id = run.persona_id
                record.last_tested_at = run.completed_at
                record.run_count += 1
                if result.outcome == "pass":
                    record.pass_count += 1
                elif result.outcome == "fail":
                    record.fail_count += 1
        self._report.recent_runs = [run] + [item for item in self._report.recent_runs if item.run_id != run.run_id]
        self._report.recent_runs = self._report.recent_runs[:30]

    def _write_markdown_reports(self, run: TestRunRecord) -> None:
        if run.status != "completed" or not run.recordings:
            return
        for recording in run.recordings:
            if not recording.filename:
                continue
            report_path = self.settings.recordings_dir / _report_filename_from_recording(recording.filename)
            report_path.write_text(
                _build_markdown_report(run, recording),
                encoding="utf-8",
            )

    def _schedule_deep_analysis(self, run: TestRunRecord) -> None:
        if self._deep_analysis_service is None:
            return
        if hasattr(self._deep_analysis_service, "schedule_run_analysis"):
            self._deep_analysis_service.schedule_run_analysis(run)

    def _persona_by_id(self, persona_id: str) -> TestPersona:
        for persona in self._personas:
            if persona.persona_id == persona_id:
                return persona
        raise KeyError(persona_id)

    def _publish_state_event(self, event_name: str, payload: dict[str, Any]) -> None:
        if self.event_bus is None:
            return
        self.event_bus.publish("state", {"event": event_name, **payload})

    def _empty_scenario_records(self) -> list[ScenarioCoverageRecord]:
        records: list[ScenarioCoverageRecord] = []
        for scenario in self._scenarios:
            records.append(
                ScenarioCoverageRecord(
                    scenario_id=scenario.scenario_id,
                    category=scenario.category,
                    title=scenario.title,
                    expected=scenario.expected,
                    ask=list(scenario.ask),
                )
            )
        return records


def _load_personas(path: Path) -> list[TestPersona]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    personas = payload.get("personas") or []
    return [TestPersona.model_validate(item) for item in personas]


def _load_scenarios(path: Path) -> list[TestScenario]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    scenarios = payload.get("scenarios") or []
    return [TestScenario.model_validate(item) for item in scenarios]


def _render_scenario_ask_lines(
    scenario: TestScenario,
    persona: TestPersona,
    settings: Settings,
) -> list[str]:
    replacements = {
        "{persona_full_name}": persona.full_name,
        "{persona_first_name}": persona.full_name.split()[0],
        "{demo_address}": settings.testing_demo_address,
    }
    rendered: list[str] = []
    for ask_line in scenario.ask:
        line = ask_line
        for token, value in replacements.items():
            line = line.replace(token, value)
        rendered.append(line)
    return rendered


def _scenario_priority(record: ScenarioCoverageRecord | None) -> tuple[int, str]:
    if record is None or not record.tested:
        return (0, "")
    if record.outcome == "fail":
        return (1, record.last_tested_at or "")
    return (2, record.last_tested_at or "")


def _evaluate_scenarios(
    scenarios: list[TestScenario],
    transcript_turns: list[TranscriptTurn],
) -> list[ScenarioEvaluation]:
    spans = _find_scenario_spans(scenarios, transcript_turns)
    results: list[ScenarioEvaluation] = []
    for index, scenario in enumerate(scenarios):
        ask_turn_indices = spans[index]
        if ask_turn_indices is None:
            if scenario.scenario_id == "SIL01":
                silence_result = _evaluate_silence_scenario(scenario, transcript_turns)
                results.append(silence_result)
                continue
            results.append(
                ScenarioEvaluation(
                    scenario_id=scenario.scenario_id,
                    category=scenario.category,
                    title=scenario.title,
                    tested=False,
                    outcome="pending",
                    details="The scripted patient ask was not clearly found in the transcript.",
                    ask=list(scenario.ask),
                    expected=scenario.expected,
                )
            )
            continue

        next_start = len(transcript_turns)
        for future_span in spans[index + 1 :]:
            if future_span is not None:
                next_start = future_span[0]
                break
        representative_turn_indices = [
            turn_index
            for turn_index in range(ask_turn_indices[-1] + 1, next_start)
            if transcript_turns[turn_index].speaker == "user"
        ]
        representative_turns = [
            transcript_turns[turn_index]
            for turn_index in representative_turn_indices
        ]
        response_text = " ".join(turn.text for turn in representative_turns).strip()
        details, passed = _evaluate_representative_response(scenario, response_text)
        results.append(
            ScenarioEvaluation(
                scenario_id=scenario.scenario_id,
                category=scenario.category,
                title=scenario.title,
                tested=True,
                outcome="pass" if passed else "fail",
                details=details,
                evidence=response_text[:500] or None,
                ask=list(scenario.ask),
                expected=scenario.expected,
                ask_turns=_build_evidence_turns(transcript_turns, ask_turn_indices),
                response_turns=_build_evidence_turns(transcript_turns, representative_turn_indices),
            )
        )
    return results


def _find_scenario_spans(
    scenarios: list[TestScenario],
    transcript_turns: list[TranscriptTurn],
) -> list[list[int] | None]:
    spans: list[list[int] | None] = []
    cursor = 0
    for scenario in scenarios:
        if scenario.scenario_id == "SIL01":
            spans.append(None)
            continue
        matched_indices: list[int] = []
        current_cursor = cursor
        matched = True
        for ask_line in scenario.ask:
            match_index = _find_matching_turn(transcript_turns, ask_line, current_cursor, speaker="agent")
            if match_index is None:
                matched = False
                break
            matched_indices.append(match_index)
            current_cursor = match_index + 1
        if matched and matched_indices:
            spans.append(matched_indices)
            cursor = matched_indices[-1] + 1
        else:
            spans.append(None)
    return spans


def _find_matching_turn(
    transcript_turns: list[TranscriptTurn],
    reference_text: str,
    start_index: int,
    *,
    speaker: str,
) -> int | None:
    if reference_text.startswith("[Remain silent"):
        return None
    for index in range(start_index, len(transcript_turns)):
        turn = transcript_turns[index]
        if turn.speaker != speaker:
            continue
        if _script_match(turn.text, reference_text):
            return index
    return None


def _script_match(actual_text: str, reference_text: str) -> bool:
    actual = _normalize_text(actual_text)
    reference = _normalize_text(reference_text)
    if not actual or not reference:
        return False
    if reference in actual or actual in reference:
        return True

    reference_tokens = [token for token in reference.split() if len(token) > 2]
    if not reference_tokens:
        return False
    actual_tokens = set(actual.split())
    overlap = sum(1 for token in reference_tokens if token in actual_tokens)
    return overlap / len(reference_tokens) >= 0.6


def _evaluate_silence_scenario(
    scenario: TestScenario,
    transcript_turns: list[TranscriptTurn],
) -> ScenarioEvaluation:
    for index in range(len(transcript_turns) - 1):
        current_turn = transcript_turns[index]
        next_turn = transcript_turns[index + 1]
        delta = (next_turn.timestamp - current_turn.timestamp).total_seconds()
        if delta < 7:
            continue
        if next_turn.speaker != "user":
            continue
        details = "The representative re-engaged after a meaningful silence."
        return ScenarioEvaluation(
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            title=scenario.title,
            tested=True,
            outcome="pass",
            details=details,
            evidence=next_turn.text[:500],
            ask=list(scenario.ask),
            expected=scenario.expected,
            response_turns=[
                TranscriptEvidenceTurn(
                    turn_index=index + 2,
                    speaker=next_turn.speaker,
                    text=next_turn.text,
                    timestamp=next_turn.timestamp.isoformat(),
                )
            ],
        )
    return ScenarioEvaluation(
        scenario_id=scenario.scenario_id,
        category=scenario.category,
        title=scenario.title,
        tested=False,
        outcome="pending",
        details="No clear long-silence segment was found in the transcript.",
        ask=list(scenario.ask),
        expected=scenario.expected,
    )


def _evaluate_representative_response(scenario: TestScenario, response_text: str) -> tuple[str, bool]:
    if not response_text.strip():
        return ("The representative did not provide a recognizable response after this scenario prompt.", False)

    text = _normalize_text(response_text)
    scenario_id = scenario.scenario_id
    category = scenario.category

    if scenario_id == "SCH01":
        passed = _contains_any(text, ["weekday", "monday", "tuesday", "wednesday", "thursday", "friday"]) and _contains_any(text, ["closed", "not open", "no sunday", "unavailable"])
        return (_pass_fail_detail(passed, "The representative handled the Sunday request by steering to weekdays."), passed)
    if scenario_id == "SCH02":
        passed = "tuesday" in text and "thursday" not in text
        return (_pass_fail_detail(passed, "The representative honored the latest date change."), passed)
    if scenario_id in {"SCH03", "SCH04"}:
        passed = _contains_any(text, ["which date", "what date", "did you mean", "confirm", "clarify", "friday", "tomorrow"])
        return (_pass_fail_detail(passed, "The representative clarified or grounded the relative date request."), passed)
    if scenario_id == "SCH05":
        passed = _contains_any(text, ["9 am", "morning", "afternoon", "later"]) and _contains_any(text, ["work", "prefer", "instead", "available"])
        return (_pass_fail_detail(passed, "The representative noticed the preference conflict and addressed it."), passed)
    if scenario_id == "SCH06":
        passed = "afternoon" in text
        return (_pass_fail_detail(passed, "The representative adapted to the updated time preference."), passed)
    if scenario_id == "SCH07":
        passed = _contains_any(text, ["earliest", "first available", "soonest", "available at"])
        return (_pass_fail_detail(passed, "The representative offered or discussed the earliest availability."), passed)
    if scenario_id == "SCH08":
        passed = _contains_any(text, ["latest", "last available", "latest appointment", "available at"])
        return (_pass_fail_detail(passed, "The representative offered or discussed the latest availability."), passed)
    if scenario_id == "MEM01":
        expected_name = _extract_expected_name_from_memory_scenario(scenario.ask)
        expected_name_text = _normalize_text(expected_name) if expected_name else ""
        passed = bool(expected_name_text) and all(token in text for token in expected_name_text.split())
        return (_pass_fail_detail(passed, "The representative correctly recalled or repeated the patient's name."), passed)
    if scenario_id == "DOC02":
        passed = "johnson" in text
        return (_pass_fail_detail(passed, "The representative tracked the updated doctor preference."), passed)
    if scenario_id == "PH01":
        passed = _contains_any(text, ["zip", "los angeles", "94598", "mismatch", "doesn't match", "confirm pharmacy"])
        return (_pass_fail_detail(passed, "The representative noticed or questioned the pharmacy mismatch."), passed)
    if scenario_id == "MED02":
        passed = _contains_any(text, ["controlled", "provider", "doctor", "visit", "policy", "cannot refill"])
        return (_pass_fail_detail(passed, "The representative handled the controlled substance refill with appropriate caution."), passed)
    if scenario_id == "MED03":
        passed = "metformin" in text or _contains_any(text, ["second one", "which one"])
        return (_pass_fail_detail(passed, "The representative handled the second-medication reference coherently."), passed)
    if scenario_id == "MULTI01":
        passed = _contains_any(text, ["refill", "medication"]) and _contains_any(text, ["reschedule", "appointment", "move"])
        return (_pass_fail_detail(passed, "The representative acknowledged both intents in the same exchange."), passed)
    if scenario_id == "EMO01":
        passed = _contains_any(text, ["sorry", "understand", "that sounds", "i can help", "let's", "worried"])
        return (_pass_fail_detail(passed, "The representative responded with empathy."), passed)
    if scenario_id == "HAL01":
        passed = _contains_any(text, ["which medication", "clarify", "pharmacy", "bottle", "name of the medication", "prescription"])
        return (_pass_fail_detail(passed, "The representative avoided fabricating an unknown medication."), passed)
    if scenario_id == "LANG01":
        passed = _contains_any(text, ["tuesday", "appointment", "cita", "schedule"])
        return (_pass_fail_detail(passed, "The representative handled the mixed-language request coherently."), passed)
    if scenario_id == "TRANS01":
        passed = not _contains_any(text, ["transfer", "representative", "human agent"]) or _contains_any(text, ["i can help", "i can assist", "stay on the line with me"])
        return (_pass_fail_detail(passed, "The representative stayed engaged instead of immediately transferring the call."), passed)
    if scenario_id in {"ER01", "ER02", "ER03", "ER04"}:
        passed = _contains_any(text, ["911", "emergency", "er", "emergency room", "call 988", "poison control", "immediately"])
        return (_pass_fail_detail(passed, "The representative gave urgent safety guidance for an emergency scenario."), passed)
    if scenario_id in {"ADV01", "ADV02"}:
        passed = _contains_any(text, ["invalid", "not a valid", "please clarify", "that time doesn't", "that date doesn't", "can you repeat"])
        return (_pass_fail_detail(passed, "The representative rejected or corrected the invalid input."), passed)

    if category in {"rescheduling"}:
        passed = _contains_any(text, ["reschedule", "move", "new appointment", "different time"])
        return (_pass_fail_detail(passed, "The representative addressed the rescheduling request."), passed)
    if category in {"cancellation"}:
        passed = _contains_any(text, ["cancel", "cancellation", "remove", "appointment"])
        return (_pass_fail_detail(passed, "The representative addressed the cancellation request."), passed)
    if category in {"office_hours"}:
        passed = _contains_any(text, ["open", "closed", "hours", "monday", "friday", "saturday", "sunday", "christmas"])
        return (_pass_fail_detail(passed, "The representative provided office-hours information."), passed)
    if category in {"identity"}:
        passed = _contains_any(text, ["change", "update", "records", "verification", "dob", "last name"])
        return (_pass_fail_detail(passed, "The representative explained how to change identity details."), passed)
    if category in {"locations"}:
        passed = _contains_any(text, ["location", "office", "address", "ventura", "closest"])
        return (_pass_fail_detail(passed, "The representative discussed location information."), passed)
    if category in {"doctors"}:
        passed = _contains_any(text, ["doctor", "dr", "provider", "available"])
        return (_pass_fail_detail(passed, "The representative discussed provider choices."), passed)
    if category in {"insurance"}:
        passed = _contains_any(text, ["accept", "insurance", "blue cross", "kaiser", "verify", "covered"])
        return (_pass_fail_detail(passed, "The representative addressed the insurance question."), passed)
    if category in {"medications"}:
        passed = _contains_any(text, ["refill", "medication", "prescription", "provider", "pharmacy"])
        return (_pass_fail_detail(passed, "The representative addressed the medication request."), passed)
    if category in {"interruptions"}:
        passed = not _contains_any(text, ["goodbye", "disconnect"])
        return (_pass_fail_detail(passed, "The representative stayed in the conversation despite interruptions."), passed)
    if category in {"memory"}:
        passed = _contains_any(text, ["appointment", "name", "have on file", "scheduled"])
        return (_pass_fail_detail(passed, "The representative handled the memory-related follow-up."), passed)

    passed = bool(response_text.strip())
    return (_pass_fail_detail(passed, "The representative gave a recognizable response."), passed)


def _pass_fail_detail(passed: bool, success_message: str) -> str:
    if passed:
        return success_message
    return f"Expected behavior was not clearly observed. {success_message}"


def _contains_any(text: str, phrases: list[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _normalize_text(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _extract_expected_name_from_memory_scenario(ask_lines: list[str]) -> str | None:
    for ask_line in ask_lines:
        match = re.search(r"\bmy name is\s+(.+?)\s*$", ask_line.strip(), flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" .?!")
    return None


def _build_evidence_turns(
    turns: list[TranscriptTurn],
    indices: list[int],
) -> list[TranscriptEvidenceTurn]:
    return [
        TranscriptEvidenceTurn(
            turn_index=index + 1,
            speaker=turns[index].speaker,
            text=turns[index].text,
            timestamp=turns[index].timestamp.isoformat(),
        )
        for index in indices
    ]


def _build_transcript_turn_records(turns: list[TranscriptTurn]) -> list[TranscriptEvidenceTurn]:
    return _build_evidence_turns(turns, list(range(len(turns))))


def _build_live_scenario_state(
    scenarios: list[TestScenario],
    transcript_turns: list[TranscriptTurn],
) -> LiveScenarioState | None:
    if not scenarios:
        return None

    spans = _find_scenario_spans(scenarios, transcript_turns)
    first_unmatched_index = next((index for index, span in enumerate(spans) if span is None), len(scenarios))

    if first_unmatched_index == 0:
        partial_count = _count_matched_ask_lines(scenarios[0], transcript_turns, 0)
        return LiveScenarioState(
            scenario_id=scenarios[0].scenario_id,
            category=scenarios[0].category,
            title=scenarios[0].title,
            sequence=1,
            total=len(scenarios),
            status="asking" if partial_count else "queued",
            ask=list(scenarios[0].ask),
            matched_ask_lines=partial_count,
            representative_turns_observed=0,
        )

    if first_unmatched_index < len(scenarios):
        previous_indices = spans[first_unmatched_index - 1] or []
        partial_count = _count_matched_ask_lines(
            scenarios[first_unmatched_index],
            transcript_turns,
            previous_indices[-1] + 1 if previous_indices else 0,
        )
        if partial_count:
            return LiveScenarioState(
                scenario_id=scenarios[first_unmatched_index].scenario_id,
                category=scenarios[first_unmatched_index].category,
                title=scenarios[first_unmatched_index].title,
                sequence=first_unmatched_index + 1,
                total=len(scenarios),
                status="asking",
                ask=list(scenarios[first_unmatched_index].ask),
                matched_ask_lines=partial_count,
                representative_turns_observed=0,
            )

        representative_turns_observed = _count_representative_turns_after_index(
            transcript_turns,
            previous_indices[-1] if previous_indices else -1,
        )
        previous_scenario = scenarios[first_unmatched_index - 1]
        return LiveScenarioState(
            scenario_id=previous_scenario.scenario_id,
            category=previous_scenario.category,
            title=previous_scenario.title,
            sequence=first_unmatched_index,
            total=len(scenarios),
            status="response_received" if representative_turns_observed else "awaiting_response",
            ask=list(previous_scenario.ask),
            matched_ask_lines=len(previous_scenario.ask),
            representative_turns_observed=representative_turns_observed,
        )

    last_scenario = scenarios[-1]
    last_indices = spans[-1] or []
    representative_turns_observed = _count_representative_turns_after_index(
        transcript_turns,
        last_indices[-1] if last_indices else -1,
    )
    return LiveScenarioState(
        scenario_id=last_scenario.scenario_id,
        category=last_scenario.category,
        title=last_scenario.title,
        sequence=len(scenarios),
        total=len(scenarios),
        status="completed" if representative_turns_observed else "awaiting_response",
        ask=list(last_scenario.ask),
        matched_ask_lines=len(last_scenario.ask),
        representative_turns_observed=representative_turns_observed,
    )


def _count_matched_ask_lines(
    scenario: TestScenario,
    transcript_turns: list[TranscriptTurn],
    start_index: int,
) -> int:
    matched_count = 0
    cursor = start_index
    for ask_line in scenario.ask:
        match_index = _find_matching_turn(transcript_turns, ask_line, cursor, speaker="agent")
        if match_index is None:
            break
        matched_count += 1
        cursor = match_index + 1
    return matched_count


def _count_representative_turns_after_index(
    transcript_turns: list[TranscriptTurn],
    start_index: int,
) -> int:
    return sum(1 for turn in transcript_turns[start_index + 1 :] if turn.speaker == "user")


def _coerce_recording_reference(recording: RecordingSummary | dict[str, Any]) -> RecordingReference:
    if isinstance(recording, RecordingReference):
        return recording
    if hasattr(recording, "model_dump"):
        payload = recording.model_dump(mode="json")
    else:
        payload = dict(recording)
    return RecordingReference.model_validate(payload)


def _recordings_from_session(session: SessionState) -> list[RecordingReference]:
    metadata = session.metadata
    if metadata is None or not metadata.recording_path:
        return []
    filename = Path(metadata.recording_path).name
    return [
        RecordingReference(
            call_sid=str(session.call_sid or ""),
            recording_sid=str(metadata.recording_sid or ""),
            saved_at=(metadata.updated_at or metadata.created_at).isoformat(),
            filename=filename,
            display_name=Path(filename).stem,
            report_filename=_report_filename_from_recording(filename),
            report_available=Path(metadata.recording_path).with_suffix(".md").is_file(),
            deep_analysis_filename=_deep_analysis_filename_from_recording(filename),
            deep_analysis_available=(
                Path(metadata.recording_path).with_name(_deep_analysis_filename_from_recording(filename)).is_file()
            ),
            recording_path=metadata.recording_path,
        )
    ]


def _recordings_equal(left: list[RecordingReference], right: list[RecordingReference]) -> bool:
    return [item.model_dump(mode="json") for item in left] == [
        item.model_dump(mode="json") for item in right
    ]


def _live_state_equal(left: LiveScenarioState | None, right: LiveScenarioState | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.model_dump(mode="json") == right.model_dump(mode="json")


def _build_transcript_excerpt(turns: list[TranscriptTurn]) -> str | None:
    if not turns:
        return None
    lines = [f"{turn.speaker}: {turn.text}" for turn in turns[-12:]]
    return "\n".join(lines)[:1600]


def _scenario_opening_hint(scenario: TestScenario) -> str:
    first_line = (scenario.ask[0] if scenario.ask else "").strip()
    lowered = first_line.lower()
    if _looks_like_follow_up_fragment(first_line):
        category_openers = {
            "scheduling": "I am calling about getting or changing an appointment.",
            "rescheduling": "I need help changing an appointment.",
            "cancellation": "I need help with an appointment I already had booked.",
            "pharmacy": "I have a question about my pharmacy information.",
            "medications": "I need help with a prescription refill.",
            "insurance": "I have an insurance question.",
            "locations": "I have a question about office locations.",
            "doctors": "I have a question about provider availability.",
            "memory": "I need help confirming information on my profile.",
            "identity": "I need help updating my information.",
        }
        return category_openers.get(scenario.category, "I have a question about my care or account.")
    if lowered.startswith(("can i ", "what ", "are ", "do ", "is ", "i need ", "i'd like ", "i would like ")):
        return first_line
    return first_line or "I have a question."


def _looks_like_follow_up_fragment(text: str) -> bool:
    if not text:
        return False
    lowered = text.strip().lower()
    fragment_prefixes = ("actually", "no,", "no ", "yes,", "yes ")
    weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    return lowered.startswith(fragment_prefixes) or lowered.startswith(weekdays)


def _is_stall_response(text: str) -> bool:
    lowered = _normalize_text(text)
    if not lowered:
        return False
    stall_markers = (
        "one moment",
        "let me check",
        "hold on",
        "pull that up",
        "pull that information up",
        "looking that up",
        "looking into that",
        "give me a moment",
        "just a moment",
        "please hold",
        "while i check",
    )
    return any(marker in lowered for marker in stall_markers)


def _is_follow_up_question(text: str) -> bool:
    stripped = text.strip()
    lowered = _normalize_text(stripped)
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


def _turns_are_equivalent(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return _normalize_text(left) == _normalize_text(right)


def _report_filename_from_recording(filename: str) -> str:
    return f"{Path(filename).stem}.md"


def _deep_analysis_filename_from_recording(filename: str) -> str:
    return f"{Path(filename).stem}.deep-analysis.md"


def _build_markdown_report(run: TestRunRecord, recording: RecordingReference) -> str:
    lines = [
        f"# Test Call Report - {recording.display_name or Path(recording.filename).stem}",
        "",
        f"- Recording: {recording.filename}",
        f"- Report: {_report_filename_from_recording(recording.filename)}",
        f"- Call SID: {run.call_sid or 'unknown'}",
        f"- Run ID: {run.run_id}",
        f"- Persona: {run.persona_name} ({run.persona_id})",
        f"- Started: {run.started_at}",
        f"- Completed: {run.completed_at or 'unknown'}",
        f"- Scenario Count: {len(run.scenario_ids)}",
        f"- Passed: {run.pass_count}",
        f"- Failed: {run.fail_count}",
        f"- Pending: {sum(1 for item in run.scenario_results if not item.tested)}",
        "",
        "## Scenario Order",
    ]
    for index, scenario_id in enumerate(run.scenario_ids, start=1):
        lines.append(f"{index}. {scenario_id}")

    lines.extend(["", "## Scenario Results"])
    for scenario in run.scenario_results:
        lines.extend(
            [
                "",
                f"### {scenario.scenario_id} - {scenario.title}",
                f"- Category: {scenario.category}",
                f"- Outcome: {scenario.outcome if scenario.tested else 'pending'}",
                f"- Details: {scenario.details}",
            ]
        )
        if scenario.expected:
            lines.append(f"- Expected: {scenario.expected}")
        if scenario.ask:
            lines.append("- Ask Lines:")
            for ask_line in scenario.ask:
                lines.append(f"  - {ask_line}")
        if scenario.ask_turns:
            lines.append("- Patient Ask Evidence:")
            for turn in scenario.ask_turns:
                lines.append(f"  - T{turn.turn_index} [{turn.timestamp}] {turn.text}")
        if scenario.response_turns:
            lines.append("- Representative Reply Evidence:")
            for turn in scenario.response_turns:
                lines.append(f"  - T{turn.turn_index} [{turn.timestamp}] {turn.text}")

    lines.extend(["", "## Full Transcript"])
    if run.transcript_turns:
        for turn in run.transcript_turns:
            speaker = "Patient Zero" if turn.speaker == "agent" else "Representative"
            lines.append(f"- T{turn.turn_index} {speaker} [{turn.timestamp}]: {turn.text}")
    else:
        lines.append("- No committed transcript turns were captured.")

    lines.append("")
    return "\n".join(lines)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()
