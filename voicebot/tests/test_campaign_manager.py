from __future__ import annotations

import random

from voicebot.src.models.call_metadata import CallMetadata
from voicebot.src.models.session_state import SessionState
from voicebot.src.testing.campaign_manager import TestCampaignManager, _render_scenario_ask_lines


def test_campaign_manager_plans_persona_and_scenarios(settings) -> None:
    settings.testing_max_scenarios_per_call = 3
    manager = TestCampaignManager(settings, rng=random.Random(7))

    run, custom_parameters = manager.plan_next_call()

    assert run.persona_id
    assert len(run.scenario_ids) == 3
    assert run.planning_mode == "auto_explore"
    assert custom_parameters["test_mode"] == "true"
    assert custom_parameters["test_run_id"] == run.run_id
    assert custom_parameters["test_planning_mode"] == "auto_explore"


def test_campaign_manager_manual_scenario_selection_uses_campaign_mode(settings) -> None:
    manager = TestCampaignManager(settings, rng=random.Random(7))

    run, custom_parameters = manager.plan_next_call(scenario_ids=["PH01", "MED02"])

    assert run.planning_mode == "campaign"
    assert run.scenario_ids == ["PH01", "MED02"]
    assert custom_parameters["test_planning_mode"] == "campaign"


def test_campaign_manager_preview_is_cached_until_start(settings) -> None:
    settings.testing_max_scenarios_per_call = 3
    manager = TestCampaignManager(settings, rng=random.Random(7))

    preview = manager.preview_next_call()
    started_run, custom_parameters = manager.plan_next_call()
    next_preview = manager.preview_next_call()

    assert started_run.run_id == preview.run_id
    assert custom_parameters["test_run_id"] == preview.run_id
    assert next_preview.run_id != preview.run_id
    assert next_preview.scenario_ids


def test_campaign_manager_finalizes_run_and_updates_report(settings) -> None:
    settings.testing_max_scenarios_per_call = 1
    settings.testing_max_scenarios_per_category = 1
    manager = TestCampaignManager(settings, rng=random.Random(3))

    run, custom_parameters = manager.plan_next_call()
    metadata = CallMetadata(
        call_sid="CA555",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    manager.activate_run(metadata)

    session = SessionState.from_metadata(metadata)
    session.append_transcript_turn("agent", "Can I come Sunday around 10 AM?")
    session.append_transcript_turn(
        "user",
        "We are closed on Sundays, but I can offer Tuesday or Wednesday instead.",
    )

    completed = manager.finalize_call(session)
    report = manager.report_snapshot()

    assert completed is not None
    assert completed.pass_count == 1
    assert completed.transcript_turns[0].turn_index == 1
    assert completed.scenario_results[0].ask_turns[0].text == "Can I come Sunday around 10 AM?"
    assert "Sundays" in completed.scenario_results[0].response_turns[0].text
    assert report["summary"]["passed_count"] >= 1
    assert report["recent_runs"][0]["run_id"] == run.run_id


def test_campaign_manager_evaluates_emergency_off_topic_derailment(settings) -> None:
    manager = TestCampaignManager(settings, rng=random.Random(3))

    run, custom_parameters = manager.plan_next_call(scenario_ids=["ER05"])
    metadata = CallMetadata(
        call_sid="CA_ER_DERAIL",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    manager.activate_run(metadata)

    session = SessionState.from_metadata(metadata)
    for ask_line in manager._active_runs["CA_ER_DERAIL"].scenario_asks["ER05"]:
        session.append_transcript_turn("agent", ask_line)
    session.append_transcript_turn(
        "user",
        "Please call 911 or go to the emergency room immediately. I cannot help with hiking, movies, or weather during chest pain.",
    )

    completed = manager.finalize_call(session)

    assert completed is not None
    assert completed.scenario_results[0].scenario_id == "ER05"
    assert completed.scenario_results[0].outcome == "pass"


def test_campaign_manager_renders_persona_name_in_memory_scenario(settings) -> None:
    manager = TestCampaignManager(settings, rng=random.Random(1))

    persona = manager._personas[0]
    scenario = manager._scenario_map["MEM01"]
    ask_lines = _render_scenario_ask_lines(scenario, persona, settings)

    assert ask_lines[0] == f"My name is {persona.full_name}."
    assert "{persona_full_name}" not in ask_lines[0]


def test_campaign_manager_updates_live_scenario_progress(settings) -> None:
    settings.testing_max_scenarios_per_call = 2
    settings.testing_max_scenarios_per_category = 1
    manager = TestCampaignManager(settings, rng=random.Random(7))

    run, custom_parameters = manager.plan_next_call()
    metadata = CallMetadata(
        call_sid="CA_PROGRESS",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    activated = manager.activate_run(metadata)
    assert activated is not None
    assert activated.current_scenario is not None

    session = SessionState.from_metadata(metadata)
    first_scenario = manager._resolved_scenarios_for_run(activated)[0]
    session.append_transcript_turn("agent", first_scenario.ask[0])
    manager.process_live_turns(session)

    current = manager.update_live_progress(session)

    assert current is not None
    assert current.scenario_id == first_scenario.scenario_id
    assert current.status in {"awaiting_response", "asking", "response_received"}


def test_campaign_manager_waits_through_hold_then_advances(settings) -> None:
    settings.testing_max_scenarios_per_call = 2
    settings.testing_max_scenarios_per_category = 1
    manager = TestCampaignManager(settings, rng=random.Random(7))

    run, custom_parameters = manager.plan_next_call()
    metadata = CallMetadata(
        call_sid="CA_FLOW",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    activated = manager.activate_run(metadata)
    assert activated is not None

    session = SessionState.from_metadata(metadata)
    first_scenario = manager._resolved_scenarios_for_run(activated)[0]
    session.append_transcript_turn("agent", first_scenario.ask[0])
    assert manager.process_live_turns(session) is None

    session.append_transcript_turn("user", "One moment while I check that for you.")
    assert manager.process_live_turns(session) is None
    assert manager._active_runs["CA_FLOW"].active_scenario_index == 0

    session.append_transcript_turn("user", "We are closed on Sundays, but I can offer Tuesday or Wednesday instead.")
    nudge = manager.process_live_turns(session)

    assert nudge is not None
    assert "next issue" in nudge.lower()
    assert manager._active_runs["CA_FLOW"].active_scenario_index == 1


def test_campaign_manager_builds_current_scenario_only_instruction(settings) -> None:
    settings.testing_max_scenarios_per_call = 2
    settings.testing_max_scenarios_per_category = 1
    manager = TestCampaignManager(settings, rng=random.Random(7))

    run, custom_parameters = manager.plan_next_call()
    metadata = CallMetadata(
        call_sid="CA_PROMPT",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    activated = manager.activate_run(metadata)
    assert activated is not None

    instruction = manager.build_call_instruction("CA_PROMPT")

    assert instruction is not None
    assert "Scenario agenda:" not in instruction
    assert activated.scenario_ids[0] in instruction
    assert activated.scenario_ids[1] not in instruction
    assert "If the representative says you need a support representative" in instruction
    assert "Do not switch to a different question" in instruction
    assert "Treat the current scenario as context and intent" in instruction
    assert "Always respond to the representative's latest direct question" in instruction
    assert "do not repeat the same big sentence again" in instruction
    assert "provide one detail at a time" in instruction
    assert "do not keep pushing just to make the scenario pass" in instruction
    assert "Do not add filler words like 'um' or 'uh'" in instruction
    assert "Incorporate natural human hesitations" not in instruction
    assert "Auto Explore mode:" in instruction
    assert "Available exploration directions:" in instruction


def test_campaign_manager_does_not_advance_on_brief_acknowledgement(settings) -> None:
    settings.testing_max_scenarios_per_call = 2
    settings.testing_max_scenarios_per_category = 1
    manager = TestCampaignManager(settings, rng=random.Random(7))

    run, custom_parameters = manager.plan_next_call()
    metadata = CallMetadata(
        call_sid="CA_ACK",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    activated = manager.activate_run(metadata)
    assert activated is not None

    session = SessionState.from_metadata(metadata)
    first_scenario = manager._resolved_scenarios_for_run(activated)[0]
    session.append_transcript_turn("agent", first_scenario.ask[0])
    assert manager.process_live_turns(session) is None

    session.append_transcript_turn("user", "Okay, let me check that for you.")
    assert manager.process_live_turns(session) is None
    assert manager._active_runs["CA_ACK"].active_scenario_index == 0


def test_campaign_manager_advances_on_clear_failed_response(settings) -> None:
    settings.testing_max_scenarios_per_call = 2
    settings.testing_max_scenarios_per_category = 1
    manager = TestCampaignManager(settings, rng=random.Random(7))

    run, custom_parameters = manager.plan_next_call()
    metadata = CallMetadata(
        call_sid="CA_FAILFAST",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    activated = manager.activate_run(metadata)
    assert activated is not None

    session = SessionState.from_metadata(metadata)
    first_scenario = manager._resolved_scenarios_for_run(activated)[0]
    session.append_transcript_turn("agent", first_scenario.ask[0])
    assert manager.process_live_turns(session) is None

    session.append_transcript_turn("user", "I can't help with that here. You need a support representative.")
    nudge = manager.process_live_turns(session)

    assert nudge is not None
    assert manager._active_runs["CA_FAILFAST"].active_scenario_index == 1


def test_campaign_manager_advances_when_passing_answer_ends_with_question(settings) -> None:
    settings.testing_max_scenarios_per_call = 2
    settings.testing_max_scenarios_per_category = 1
    manager = TestCampaignManager(settings, rng=random.Random(7))

    run, custom_parameters = manager.plan_next_call(scenario_ids=["PH01", "MED02"])
    metadata = CallMetadata(
        call_sid="CA_PASS_QUESTION",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    activated = manager.activate_run(metadata)
    assert activated is not None

    session = SessionState.from_metadata(metadata)
    session.append_transcript_turn("agent", "I use CVS in Los Angeles. Zip code 94598.")
    assert manager.process_live_turns(session) is None

    session.append_transcript_turn(
        "user",
        "That zip code looks like a mismatch for Los Angeles. Do you want me to confirm the pharmacy?",
    )
    nudge = manager.process_live_turns(session)

    assert nudge is not None
    assert manager._active_runs["CA_PASS_QUESTION"].active_scenario_index == 1


def test_campaign_manager_advances_when_failure_answer_ends_with_question(settings) -> None:
    settings.testing_max_scenarios_per_call = 2
    settings.testing_max_scenarios_per_category = 1
    manager = TestCampaignManager(settings, rng=random.Random(7))

    run, custom_parameters = manager.plan_next_call(scenario_ids=["PH01", "MED02"])
    metadata = CallMetadata(
        call_sid="CA_FAIL_QUESTION",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    activated = manager.activate_run(metadata)
    assert activated is not None

    session = SessionState.from_metadata(metadata)
    session.append_transcript_turn("agent", "I use CVS in Los Angeles. Zip code 94598.")
    assert manager.process_live_turns(session) is None

    session.append_transcript_turn(
        "user",
        "I can connect you to our patient support team to review pharmacy options. Are you still there?",
    )
    nudge = manager.process_live_turns(session)

    assert nudge is not None
    assert manager._active_runs["CA_FAIL_QUESTION"].active_scenario_index == 1


def test_campaign_manager_auto_explore_reorders_next_scenario_from_context(settings) -> None:
    manager = TestCampaignManager(settings, rng=random.Random(7))

    run, custom_parameters = manager.plan_next_call(scenario_ids=["HRS02", "INS01", "DOC01"])
    metadata = CallMetadata(
        call_sid="CA_AUTO_REORDER",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    activated = manager.activate_run(metadata)
    assert activated is not None
    activated.planning_mode = "auto_explore"

    session = SessionState.from_metadata(metadata)
    for ask_line in activated.scenario_asks["HRS02"]:
        session.append_transcript_turn("agent", ask_line)
    assert manager.process_live_turns(session) is None

    session.append_transcript_turn(
        "user",
        "We are closed on Saturdays, but we have doctor openings with Dr. Adams next week.",
    )
    nudge = manager.process_live_turns(session)

    assert nudge is not None
    assert manager._active_runs["CA_AUTO_REORDER"].scenario_ids[1] == "DOC01"
    assert manager._active_runs["CA_AUTO_REORDER"].current_scenario.scenario_id == "DOC01"


def test_campaign_manager_advances_on_pretty_good_ai_response(settings) -> None:
    settings.testing_max_scenarios_per_call = 2
    settings.testing_max_scenarios_per_category = 1
    manager = TestCampaignManager(settings, rng=random.Random(7))

    run, custom_parameters = manager.plan_next_call()
    metadata = CallMetadata(
        call_sid="CA_AI",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    activated = manager.activate_run(metadata)
    assert activated is not None

    session = SessionState.from_metadata(metadata)
    first_scenario = manager._resolved_scenarios_for_run(activated)[0]
    session.append_transcript_turn("agent", first_scenario.ask[0])
    assert manager.process_live_turns(session) is None

    session.append_transcript_turn("user", "I am a pretty good AI, but I cannot help with that.")
    nudge = manager.process_live_turns(session)

    assert nudge is not None
    assert manager._active_runs["CA_AI"].active_scenario_index == 1


def test_campaign_manager_manual_transition_next_and_select(settings) -> None:
    settings.testing_max_scenarios_per_call = 3
    settings.testing_max_scenarios_per_category = 1
    manager = TestCampaignManager(settings, rng=random.Random(7))

    run, custom_parameters = manager.plan_next_call()
    metadata = CallMetadata(
        call_sid="CA_MANUAL",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    activated = manager.activate_run(metadata)
    assert activated is not None
    assert len(activated.scenario_ids) >= 3

    next_transition = manager.transition_active_scenario(call_sid="CA_MANUAL", mode="next")
    assert next_transition["current_scenario"]["scenario_id"] == activated.scenario_ids[1]
    assert next_transition["transition_nudge"]

    selected_id = activated.scenario_ids[2]
    selected_transition = manager.transition_active_scenario(
        call_sid="CA_MANUAL",
        mode="select",
        target_scenario_id=selected_id,
        reason="operator_test",
    )

    assert selected_transition["current_scenario"]["scenario_id"] == selected_id
    assert manager._active_runs["CA_MANUAL"].active_scenario_index == 2
    assert manager._active_runs["CA_MANUAL"].active_status == "queued"


def test_campaign_manager_reset_clears_coverage_but_keeps_history(settings) -> None:
    settings.testing_max_scenarios_per_call = 1
    settings.testing_max_scenarios_per_category = 1
    manager = TestCampaignManager(settings, rng=random.Random(3))

    initial_run, _ = manager.plan_next_call()
    initial_scenarios = list(initial_run.scenario_ids)
    run, custom_parameters = manager.plan_next_call()
    metadata = CallMetadata(
        call_sid="CA_RESET",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
    )
    manager.activate_run(metadata)

    session = SessionState.from_metadata(metadata)
    scenario = manager._resolved_scenarios_for_run(manager._active_runs["CA_RESET"])[0]
    session.append_transcript_turn("agent", scenario.ask[0])
    session.append_transcript_turn("user", "That works, I can help with that.")
    manager.finalize_call(session)

    before_reset = manager.report_snapshot()
    assert before_reset["summary"]["tested_count"] >= 1
    assert before_reset["recent_runs"]

    manager.reset_campaign()
    after_reset = manager.report_snapshot()
    next_run, _ = manager.plan_next_call()

    assert after_reset["summary"]["tested_count"] == 0
    assert after_reset["summary"]["pending_count"] == after_reset["summary"]["total_scenarios"]
    assert after_reset["recent_runs"]
    assert next_run.scenario_ids == initial_scenarios


def test_campaign_manager_generates_markdown_report_when_recording_available(settings) -> None:
    settings.testing_max_scenarios_per_call = 1
    settings.testing_max_scenarios_per_category = 1
    manager = TestCampaignManager(settings, rng=random.Random(3))

    run, custom_parameters = manager.plan_next_call()
    metadata = CallMetadata(
        call_sid="CA_REPORT",
        to_number="+15555550124",
        from_number="+15555550123",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters=custom_parameters,
        recording_sid="RE900",
        recording_path=str(settings.recordings_dir / "recording-0001.mp3"),
    )
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    (settings.recordings_dir / "recording-0001.mp3").write_bytes(b"audio")
    manager.activate_run(metadata)

    session = SessionState.from_metadata(metadata)
    scenario = manager._resolved_scenarios_for_run(manager._active_runs["CA_REPORT"])[0]
    session.append_transcript_turn("agent", scenario.ask[0])
    session.append_transcript_turn("user", "We are closed on Sundays, but I can offer Tuesday or Wednesday instead.")
    manager.finalize_call(session)

    report_path = settings.recordings_dir / "recording-0001.md"
    assert report_path.is_file()
    assert "Test Call Report" in report_path.read_text(encoding="utf-8")
