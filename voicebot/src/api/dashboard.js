const state = {
  events: [],
  activeCalls: [],
  recentCalls: [],
  recordings: [],
  hidePackets: true,
  socket: null,
  transcriptMessageMap: new Map(),
  snapshotRefreshTimer: null,
  testingRefreshTimer: null,
  recordingsRefreshTimer: null,
  recordingsPollTimer: null,
  activeTab: "live",
  testing: {
    summary: {
      total_scenarios: 0,
      tested_count: 0,
      passed_count: 0,
      failed_count: 0,
      pending_count: 0,
    },
    scenario_results: [],
    recent_runs: [],
    active_runs: [],
    next_run_preview: null,
  },
  availablePersonas: [],
  availableScenarios: [],
};

const packetEvents = new Set([
  "audio_bridge_twilio_media_forwarded",
  "audio_bridge_gemini_media_forwarded",
  "gemini_audio_sent",
]);

const snapshotRelevantEvents = new Set([
  "call_manager_call_registered",
  "call_manager_stream_attached",
  "call_manager_status_updated",
  "call_manager_call_completed",
  "call_manager_call_failed",
  "twilio_status_callback_received",
  "twilio_websocket_stop_received",
  "twilio_recording_status_received",
  "twilio_stream_status_received",
]);

const recordingRelevantEvents = new Set([
  "twilio_recording_status_received",
  "recording_saved",
  "recording_metadata_saved",
  "recording_favorite_updated",
]);

const testingRelevantEvents = new Set([
  "testing_run_started",
  "testing_progress_updated",
  "testing_scenario_transition_requested",
  "testing_scenario_transition_queued",
  "testing_campaign_reset",
  "testing_report_updated",
]);

const els = {
  startCall: document.getElementById("start-call"),
  testingStartCall: document.getElementById("testing-start-call"),
  testingReset: document.getElementById("testing-reset"),
  hangupCall: document.getElementById("hangup-call"),
  refresh: document.getElementById("refresh-dashboard"),
  statusNote: document.getElementById("status-note"),
  connectionDot: document.getElementById("connection-dot"),
  connectionLabel: document.getElementById("connection-label"),
  footerConnectionDot: document.getElementById("footer-connection-dot"),
  footerConnectionLabel: document.getElementById("footer-connection-label"),
  metricActiveCalls: document.getElementById("metric-active-calls"),
  metricEventCount: document.getElementById("metric-event-count"),
  publicUrl: document.getElementById("meta-public-url"),
  streamUrl: document.getElementById("meta-stream-url"),
  callList: document.getElementById("call-list"),
  recordingList: document.getElementById("recording-list"),
  testingActiveRuns: document.getElementById("testing-active-runs"),
  testingRecentRuns: document.getElementById("testing-recent-runs"),
  testingScenarioList: document.getElementById("testing-scenario-list"),
  testingPreview: document.getElementById("testing-preview"),
  metricTestedTotal: document.getElementById("metric-tested-total"),
  metricTestedPass: document.getElementById("metric-tested-pass"),
  metricTestedFail: document.getElementById("metric-tested-fail"),
  metricTestedPending: document.getElementById("metric-tested-pending"),
  transcriptList: document.getElementById("transcript-list"),
  liveScenarioStrip: document.getElementById("live-scenario-strip"),
  logList: document.getElementById("log-list"),
  togglePackets: document.getElementById("toggle-packets"),
  personaSelect: document.getElementById("persona-select"),
  scenarioChecklist: document.getElementById("scenario-checklist"),
  scenarioHint: document.getElementById("scenario-hint"),
  scenarioSelectAll: document.getElementById("scenario-select-all"),
  scenarioSelectNone: document.getElementById("scenario-select-none"),
  tabButtons: Array.from(document.querySelectorAll(".tab-button")),
  panels: Array.from(document.querySelectorAll(".panel")),
};

function setConnectionState(label, cssClass) {
  els.connectionLabel.textContent = label;
  els.connectionDot.className = `dot ${cssClass}`;
  els.footerConnectionLabel.textContent = label;
  els.footerConnectionDot.className = `dot ${cssClass}`;
}

function eventName(event) {
  return event?.payload?.event || "unknown_event";
}

function formatTime(value) {
  if (!value) return "unknown";
  try {
    return new Date(value).toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return value;
  }
}

function setActiveTab(tabName) {
  state.activeTab = tabName;
  for (const button of els.tabButtons) {
    const isActive = button.dataset.tab === tabName;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-selected", String(isActive));
  }
  for (const panel of els.panels) {
    panel.classList.toggle("active", panel.id === `panel-${tabName}`);
  }
  syncRecordingsPolling();
}

function renderCalls() {
  const calls = state.activeCalls.length ? state.activeCalls : state.recentCalls;
  if (!calls.length) {
    els.callList.innerHTML = '<div class="empty">No call activity yet.</div>';
    return;
  }

  els.callList.innerHTML = calls.map((call) => {
    const metadata = call.metadata || {};
    return `
      <article class="call-item">
        <strong>${escapeHtml(call.status || "unknown")} · ${escapeHtml(call.call_sid || "no call sid")}</strong>
        <div class="call-meta">to=${escapeHtml(metadata.to_number || "unknown")}
stream=${escapeHtml(call.stream_sid || "pending")}
started=${escapeHtml(formatTime(call.connected_at || call.created_at))}</div>
      </article>
    `;
  }).join("");
}

function renderLogs() {
  const events = state.events
    .filter((entry) => entry.kind === "log")
    .filter((entry) => !state.hidePackets || !packetEvents.has(eventName(entry)))
    .slice(-200)
    .reverse();

  if (!events.length) {
    els.logList.innerHTML = '<div class="empty">No logs yet.</div>';
    return;
  }

  els.logList.innerHTML = events.map((entry) => {
    const payload = { ...entry.payload };
    const title = payload.event || "log";
    delete payload.event;
    return `
      <article class="log-item">
        <strong>${escapeHtml(title)}</strong>
        <div class="log-meta">${escapeHtml((entry.payload.level || "info").toUpperCase())} · ${escapeHtml(formatTime(entry.payload.timestamp || entry.timestamp))}</div>
        <div class="log-payload">${escapeHtml(JSON.stringify(payload, null, 2))}</div>
      </article>
    `;
  }).join("");
}

function renderRecordings() {
  if (!state.recordings.length) {
    els.recordingList.innerHTML = '<div class="empty">No recordings yet.</div>';
    return;
  }

  els.recordingList.innerHTML = state.recordings.map((recording) => `
    <article class="recording-card">
      <div class="recording-title">
        <strong>${escapeHtml(recording.display_name || recording.recording_sid || "recording")}</strong>
        <div class="recording-title-meta">
          ${recording.favorite ? '<span class="chip favorite-chip">Favorite</span>' : ""}
          <span class="chip">${escapeHtml(formatTime(recording.saved_at))}</span>
        </div>
      </div>
      <div class="recording-meta">call=${escapeHtml(recording.call_sid || "unknown")}
file=${escapeHtml(recording.filename || "unknown")}</div>
      <audio class="recording-audio" controls preload="none" src="${escapeAttribute(`/api/recordings/${recording.filename}`)}"></audio>
      ${renderRecordingActions(recording)}
    </article>
  `).join("");
}

function testingSpeakerLabel(speaker) {
  return speaker === "agent" ? "Patient Zero" : "Representative";
}

function formatScenarioStatus(status) {
  switch (status) {
    case "asking":
      return "Asking";
    case "awaiting_response":
      return "Awaiting Reply";
    case "response_received":
      return "Advancing";
    case "completed":
      return "Completed";
    default:
      return "Queued";
  }
}

function renderRecordingLinks(recordings) {
  if (!recordings || !recordings.length) {
    return '<div class="testing-mini-empty">Recording pending.</div>';
  }

  return `
    <div class="testing-recordings">
      ${recordings.map((recording) => `
        <div class="testing-recording-pill">
          <a class="recording-link" href="${escapeAttribute(`/api/recordings/${recording.filename}`)}" target="_blank" rel="noopener noreferrer">
            ${escapeHtml(recording.display_name || recording.filename || recording.recording_sid || "recording")}
          </a>
          ${recording.report_available ? `
            <a class="recording-link subtle" href="${escapeAttribute(`/api/recordings/${recording.filename}/report`)}" target="_blank" rel="noopener noreferrer">Report</a>
          ` : ""}
          ${recording.deep_analysis_available ? `
            <a class="recording-link subtle" href="${escapeAttribute(`/api/recordings/${recording.filename}/deep-analysis`)}" target="_blank" rel="noopener noreferrer">Deep Analysis</a>
          ` : ""}
        </div>
      `).join("")}
    </div>
  `;
}

function renderScenarioTransitionControls(run) {
  const scenarioPlan = run.scenario_plan || [];
  if (!run.call_sid || scenarioPlan.length <= 1) {
    return "";
  }
  const currentId = run.current_scenario?.scenario_id || "";
  return `
    <div class="scenario-transition-controls">
      <button
        class="secondary compact"
        type="button"
        data-scenario-transition="next"
        data-call-sid="${escapeAttribute(run.call_sid)}"
      >Next Scenario</button>
      <select class="config-select compact-select" data-scenario-select="${escapeAttribute(run.call_sid)}" aria-label="Change active scenario">
        ${scenarioPlan.map((scenario) => `
          <option value="${escapeAttribute(scenario.scenario_id)}" ${scenario.scenario_id === currentId ? "selected" : ""}>
            ${escapeHtml(scenario.scenario_id)} · ${escapeHtml(scenario.title || "Scenario")}
          </option>
        `).join("")}
      </select>
      <button
        class="secondary compact"
        type="button"
        data-scenario-transition="select"
        data-call-sid="${escapeAttribute(run.call_sid)}"
      >Change Scenario</button>
    </div>
  `;
}

function renderRecordingActions(recording) {
  return `
    <div class="recording-actions">
      <button
        class="recording-favorite-button ${recording.favorite ? "active" : ""}"
        type="button"
        data-recording-favorite
        data-filename="${escapeAttribute(recording.filename)}"
        data-favorite="${recording.favorite ? "true" : "false"}"
      >${recording.favorite ? "Unfavorite" : "Favorite"}</button>
      <a class="recording-link" href="${escapeAttribute(`/api/recordings/${recording.filename}`)}" target="_blank" rel="noopener noreferrer">Open Audio</a>
      ${recording.report_available ? `
        <a class="recording-link subtle" href="${escapeAttribute(`/api/recordings/${recording.filename}/report`)}" target="_blank" rel="noopener noreferrer">Open Report</a>
      ` : ""}
      ${recording.deep_analysis_available ? `
        <a class="recording-link subtle" href="${escapeAttribute(`/api/recordings/${recording.filename}/deep-analysis`)}" target="_blank" rel="noopener noreferrer">Open Deep Analysis</a>
      ` : ""}
    </div>
  `;
}

function renderTranscriptEvidence(turns) {
  if (!turns || !turns.length) {
    return '<div class="testing-mini-empty">No exact transcript match captured yet.</div>';
  }

  return `
    <div class="testing-turn-list">
      ${turns.map((turn) => `
        <div class="testing-turn">
          <div class="testing-turn-meta">
            <span class="turn-badge">T${escapeHtml(String(turn.turn_index || "?"))}</span>
            <span>${escapeHtml(testingSpeakerLabel(turn.speaker))}</span>
            <span>${escapeHtml(formatTime(turn.timestamp))}</span>
          </div>
          <div class="testing-turn-text">${escapeHtml(turn.text || "")}</div>
        </div>
      `).join("")}
    </div>
  `;
}

function renderRunSummary(run) {
  return `${escapeHtml(run.persona_name || run.persona_id || "persona")}
run=${escapeHtml(run.run_id || "unknown")}
call=${escapeHtml(run.call_sid || "unknown")}
tested=${escapeHtml(String((run.scenario_results || []).filter((item) => item.tested).length || 0))}`;
}

function renderScenarioEvidenceCard(scenario) {
  return `
    <article class="scenario-item">
      <div class="testing-title">
        <strong>${escapeHtml(scenario.scenario_id)} · ${escapeHtml(scenario.title || "Scenario")}</strong>
        <span class="chip">${escapeHtml(scenario.category || "general")}</span>
        <span class="status-tag ${escapeAttribute(scenario.outcome || "pending")}">${escapeHtml(scenario.outcome || "pending")}</span>
      </div>
      <div class="testing-copy">${escapeHtml(scenario.details || "No details yet.")}</div>
      <div class="testing-evidence-grid">
        <div class="testing-stage">
          <div class="testing-stage-label">Patient Ask</div>
          ${renderTranscriptEvidence(scenario.ask_turns || [])}
        </div>
        <div class="testing-stage">
          <div class="testing-stage-label">Representative Reply</div>
          ${renderTranscriptEvidence(scenario.response_turns || [])}
        </div>
      </div>
    </article>
  `;
}

function renderPreviewScenarioCard(scenario, index) {
  const askLines = scenario.ask || [];
  return `
    <article class="scenario-item preview">
      <div class="testing-title">
        <strong>${escapeHtml(scenario.scenario_id || "scenario")} · ${escapeHtml(scenario.title || "Scenario")}</strong>
        <span class="chip">${escapeHtml(scenario.category || "general")}</span>
        <span class="chip">Step ${escapeHtml(String(index + 1))}</span>
      </div>
      ${askLines.length ? `
        <div class="testing-ask-list">
          ${askLines.map((line) => `<div class="testing-ask-line">${escapeHtml(line)}</div>`).join("")}
        </div>
      ` : '<div class="testing-mini-empty">No ask lines available.</div>'}
    </article>
  `;
}

function renderTesting() {
  const summary = state.testing.summary || {};
  const activeRuns = state.testing.active_runs || [];
  const recentRuns = state.testing.recent_runs || [];
  const nextRunPreview = state.testing.next_run_preview || null;
  const scenarioResults = [...(state.testing.scenario_results || [])].sort((left, right) => {
    const priority = (scenario) => {
      if (!scenario.tested) return 0;
      if (scenario.outcome === "fail") return 1;
      if (scenario.outcome === "pass") return 2;
      return 3;
    };
    return priority(left) - priority(right) || String(left.scenario_id || "").localeCompare(String(right.scenario_id || ""));
  });

  els.metricTestedTotal.textContent = String(summary.total_scenarios || 0);
  els.metricTestedPass.textContent = String(summary.passed_count || 0);
  els.metricTestedFail.textContent = String(summary.failed_count || 0);
  els.metricTestedPending.textContent = String(summary.pending_count || 0);

  if (!nextRunPreview || !(nextRunPreview.scenario_ids || []).length) {
    els.testingPreview.innerHTML = '<div class="empty">Next call preview unavailable.</div>';
  } else {
    const planningMode = nextRunPreview.planning_mode || "campaign";
    const isAutoExplore = planningMode === "auto_explore";
    els.testingPreview.innerHTML = `
      <article class="testing-card preview-card">
        <div class="testing-title">
          <strong>${escapeHtml(nextRunPreview.persona_name || nextRunPreview.persona_id || "persona")}</strong>
          <span class="chip">${escapeHtml(nextRunPreview.persona_id || "persona")}</span>
          <span class="status-tag pending">${escapeHtml(isAutoExplore ? "Auto Explore" : "Campaign")}</span>
          <span class="chip">${escapeHtml(String((nextRunPreview.scenario_ids || []).length))} Candidates</span>
        </div>
        <div class="testing-copy">${escapeHtml(isAutoExplore
          ? "The next call starts with this candidate pool, then follows the support flow and reorders scenarios when the conversation makes another boundary more relevant."
          : "The next call will use this exact persona and scenario order."
        )}</div>
        <div class="testing-grid">
          ${(nextRunPreview.scenario_plan || []).map((scenario, index) => renderPreviewScenarioCard(scenario, index)).join("")}
        </div>
      </article>
    `;
  }

  if (!activeRuns.length) {
    els.testingActiveRuns.innerHTML = '<div class="empty">No active test call right now.</div>';
  } else {
    els.testingActiveRuns.innerHTML = activeRuns.map((run) => `
      <article class="testing-card">
        <div class="testing-title">
          <strong>Active Run · ${escapeHtml(run.persona_name || run.persona_id || "persona")}</strong>
          <span class="status-tag pending">In Call</span>
        </div>
        <div class="testing-copy">run=${escapeHtml(run.run_id || "unknown")}
scenarios=${escapeHtml(String((run.scenario_ids || []).length || 0))}
started=${escapeHtml(formatTime(run.started_at))}</div>
        <div class="testing-stage">
          <div class="testing-stage-label">Current Scenario</div>
          ${run.current_scenario ? `
            <div class="testing-title">
              <strong>${escapeHtml(run.current_scenario.scenario_id)} · ${escapeHtml(run.current_scenario.title || "Scenario")}</strong>
              <span class="chip">${escapeHtml(run.current_scenario.category || "general")}</span>
              <span class="status-tag pending">${escapeHtml(formatScenarioStatus(run.current_scenario.status))}</span>
            </div>
            <div class="testing-copy">step ${escapeHtml(String(run.current_scenario.sequence || 1))} of ${escapeHtml(String(run.current_scenario.total || 1))}
matched asks=${escapeHtml(String(run.current_scenario.matched_ask_lines || 0))}
rep turns=${escapeHtml(String(run.current_scenario.representative_turns_observed || 0))}</div>
            <div class="testing-ask-list">
              ${(run.current_scenario.ask || []).map((line) => `<div class="testing-ask-line">${escapeHtml(line)}</div>`).join("")}
            </div>
            ${renderScenarioTransitionControls(run)}
          ` : '<div class="testing-mini-empty">Scenario progress is waiting for committed transcript turns.</div>'}
        </div>
        <div class="testing-stage">
          <div class="testing-stage-label">Linked Recordings</div>
          ${renderRecordingLinks(run.recordings || [])}
        </div>
      </article>
    `).join("");
  }

  if (!recentRuns.length) {
    els.testingRecentRuns.innerHTML = '<div class="empty">Completed test calls will appear here.</div>';
  } else {
    els.testingRecentRuns.innerHTML = recentRuns.slice(0, 8).map((run) => `
      <article class="testing-run-card history">
        <div class="testing-row">
          <div class="testing-copy">${renderRunSummary(run)}</div>
          <div class="testing-meta">${escapeHtml(formatTime(run.completed_at || run.started_at))}</div>
          <div><span class="status-tag ${run.fail_count ? "fail" : "pass"}">${run.fail_count ? `${run.fail_count} Fail` : `${run.pass_count || 0} Pass`}</span></div>
        </div>
        <div class="testing-stage">
          <div class="testing-stage-label">Recordings</div>
          ${renderRecordingLinks(run.recordings || [])}
        </div>
        <details class="testing-disclosure">
          <summary>Scenario Results (${escapeHtml(String((run.scenario_results || []).length || 0))})</summary>
          <div class="testing-disclosure-body">
            ${(run.scenario_results || []).map(renderScenarioEvidenceCard).join("")}
          </div>
        </details>
        <details class="testing-disclosure">
          <summary>Exact Transcript (${escapeHtml(String((run.transcript_turns || []).length || 0))} turns)</summary>
          <div class="testing-disclosure-body">
            ${renderTranscriptEvidence(run.transcript_turns || [])}
          </div>
        </details>
      </article>
    `).join("");
  }

  if (!scenarioResults.length) {
    els.testingScenarioList.innerHTML = '<div class="empty">Scenario coverage will appear here after the first test call.</div>';
    return;
  }

  els.testingScenarioList.innerHTML = `
    <article class="testing-card">
      <div class="testing-title">
        <strong>Scenario Coverage</strong>
      </div>
      <div class="testing-grid">
        ${scenarioResults.map((scenario) => `
          <div class="scenario-item">
            <div class="testing-row">
              <div>
                <div class="testing-title">
                  <strong>${escapeHtml(scenario.scenario_id)} · ${escapeHtml(scenario.title || "Scenario")}</strong>
                  <span class="chip">${escapeHtml(scenario.category || "general")}</span>
                </div>
                <div class="testing-copy">${escapeHtml(scenario.details || "No details yet.")}</div>
              </div>
              <div class="testing-meta">Tested: ${scenario.tested ? "Yes" : "No"}</div>
              <div><span class="status-tag ${escapeAttribute(scenario.outcome || "pending")}">${escapeHtml(scenario.outcome || "pending")}</span></div>
            </div>
            ${scenario.last_run_id ? `<div class="testing-copy">last run=${escapeHtml(scenario.last_run_id)} · persona=${escapeHtml(scenario.last_persona_id || "unknown")}</div>` : ""}
          </div>
        `).join("")}
      </div>
    </article>
  `;

  if (els.scenarioChecklist) {
    for (const result of scenarioResults) {
      if (!result.tested && result.outcome === 'pending') continue;
      const checkbox = els.scenarioChecklist.querySelector(`input[value="${escapeAttribute(result.scenario_id)}"]`);
      if (checkbox) {
        const itemDiv = checkbox.closest('.scenario-check-item');
        if (itemDiv) {
          itemDiv.classList.remove('scenario-outcome-pass', 'scenario-outcome-fail', 'scenario-outcome-pending');
          itemDiv.classList.add(`scenario-outcome-${result.outcome}`);
          
          let badge = itemDiv.querySelector('.outcome-badge');
          if (!badge) {
             badge = document.createElement('span');
             itemDiv.appendChild(badge);
          }
          badge.className = `outcome-badge ${result.outcome}`;
          badge.textContent = result.outcome;
        }
      }
    }
  }
}

function renderLiveScenario() {
  if (!els.liveScenarioStrip) return;
  const run = (state.testing.active_runs || [])[0];
  const scenario = run?.current_scenario || null;
  if (!run || !scenario) {
    els.liveScenarioStrip.innerHTML = `
      <div class="live-scenario-empty">
        <span>No active scenario</span>
      </div>
    `;
    return;
  }

  els.liveScenarioStrip.innerHTML = `
    <div class="live-scenario-copy">
      <span class="chip">Scenario ${escapeHtml(String(scenario.sequence || 1))}/${escapeHtml(String(scenario.total || 1))}</span>
      <strong>${escapeHtml(scenario.scenario_id)} · ${escapeHtml(scenario.title || "Scenario")}</strong>
      <span class="status-tag pending">${escapeHtml(formatScenarioStatus(scenario.status))}</span>
    </div>
    ${renderScenarioTransitionControls(run)}
  `;
}

function currentLiveCall() {
  return state.activeCalls[0] || null;
}

function render() {
  els.metricActiveCalls.textContent = String(state.activeCalls.length);
  els.metricEventCount.textContent = String(state.events.length);
  els.hangupCall.disabled = !currentLiveCall();
  renderCalls();
  renderRecordings();
  renderTesting();
  renderLiveScenario();
  renderLogs();
}

function applyTestingReport(report) {
  state.testing = report || state.testing;
}

async function loadRecordings() {
  const response = await fetch("/api/recordings");
  if (!response.ok) throw new Error(`recordings failed: ${response.status}`);
  state.recordings = await response.json();
  renderRecordings();
}

async function toggleRecordingFavorite(button) {
  const filename = button.dataset.filename;
  const currentlyFavorite = button.dataset.favorite === "true";
  if (!filename) {
    return;
  }

  button.disabled = true;
  els.statusNote.textContent = `${currentlyFavorite ? "Removing" : "Adding"} favorite flag...`;
  try {
    const response = await fetch(`/api/recordings/${encodeURIComponent(filename)}/favorite`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ favorite: !currentlyFavorite }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "favorite update failed");
    }
    state.recordings = state.recordings.map((recording) =>
      recording.filename === payload.recording.filename ? payload.recording : recording
    );
    state.recordings.sort((left, right) => {
      if (left.favorite !== right.favorite) {
        return left.favorite ? -1 : 1;
      }
      return String(right.saved_at || "").localeCompare(String(left.saved_at || ""));
    });
    renderRecordings();
    els.statusNote.textContent = payload.recording.favorite
      ? `Favorited ${payload.recording.display_name || payload.recording.filename}.`
      : `Removed favorite from ${payload.recording.display_name || payload.recording.filename}.`;
  } catch (error) {
    els.statusNote.textContent = `Favorite update failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function loadSnapshot() {
  const response = await fetch("/api/dashboard/snapshot");
  if (!response.ok) throw new Error(`snapshot failed: ${response.status}`);
  const payload = await response.json();
  state.events = payload.events || [];
  state.activeCalls = payload.active_calls || [];
  state.recentCalls = payload.recent_calls || [];
  state.recordings = payload.recordings || [];
  applyTestingReport(payload.testing || state.testing);
  els.publicUrl.textContent = payload.app.public_base_url || "Not configured";
  els.streamUrl.textContent = payload.app.stream_url || "Unavailable";
  hydrateTranscriptMessages(payload.transcript_messages || []);
  render();
}

async function loadTestingReport() {
  const response = await fetch("/api/testing/report");
  if (!response.ok) throw new Error(`testing report failed: ${response.status}`);
  applyTestingReport(await response.json());
  renderTesting();
  renderLiveScenario();
}

function getSelectedPersonaId() {
  if (!els.personaSelect) return null;
  const value = els.personaSelect.value;
  return value || null;
}

function getSelectedScenarioIds() {
  if (!els.scenarioChecklist) return null;
  const checked = Array.from(
    els.scenarioChecklist.querySelectorAll('input[type="checkbox"]:checked')
  ).map((input) => input.value);
  return checked.length > 0 ? checked : null;
}

function updateScenarioHint() {
  if (!els.scenarioHint || !els.scenarioChecklist) return;
  const total = els.scenarioChecklist.querySelectorAll('input[type="checkbox"]').length;
  const checked = els.scenarioChecklist.querySelectorAll('input[type="checkbox"]:checked').length;
  if (checked === 0) {
    els.scenarioHint.textContent = "None selected \u00b7 auto-select";
  } else if (checked === total) {
    els.scenarioHint.textContent = `All ${total} selected`;
  } else {
    els.scenarioHint.textContent = `${checked} of ${total} selected`;
  }
}

function setAllScenarioCheckboxes(checked) {
  if (!els.scenarioChecklist) return;
  for (const input of els.scenarioChecklist.querySelectorAll('input[type="checkbox"]')) {
    input.checked = checked;
  }
  updateScenarioHint();
}

async function loadSelectors() {
  try {
    const [personasRes, scenariosRes] = await Promise.all([
      fetch("/api/testing/personas"),
      fetch("/api/testing/scenarios"),
    ]);
    if (personasRes.ok) {
      state.availablePersonas = await personasRes.json();
    }
    if (scenariosRes.ok) {
      state.availableScenarios = await scenariosRes.json();
    }
    renderSelectors();
  } catch {
    // selectors remain empty on error, auto-select still works
  }
}

function renderSelectors() {
  if (els.personaSelect && state.availablePersonas.length) {
    const current = els.personaSelect.value;
    els.personaSelect.innerHTML = '<option value="">Auto (random)</option>';
    for (const persona of state.availablePersonas) {
      const option = document.createElement("option");
      option.value = persona.persona_id;
      option.textContent = `${persona.full_name} (${persona.persona_id})`;
      els.personaSelect.appendChild(option);
    }
    if (current) {
      els.personaSelect.value = current;
    }
  }

  if (els.scenarioChecklist && state.availableScenarios.length) {
    const previouslyChecked = new Set(
      Array.from(
        els.scenarioChecklist.querySelectorAll('input[type="checkbox"]:checked')
      ).map((input) => input.value)
    );

    const groups = new Map();
    for (const scenario of state.availableScenarios) {
      if (!groups.has(scenario.category)) {
        groups.set(scenario.category, []);
      }
      groups.get(scenario.category).push(scenario);
    }

    els.scenarioChecklist.innerHTML = "";
    for (const [category, scenarios] of groups) {
      const group = document.createElement("div");
      group.className = "scenario-category-group";
      group.dataset.open = "true";

      const header = document.createElement("div");
      header.className = "scenario-category-header";
      header.innerHTML = `
        <span class="category-chevron">&#9662;</span>
        <span class="category-name">${escapeHtml(category.replace(/_/g, " "))}</span>
        <span class="category-count">${escapeHtml(String(scenarios.length))}</span>
      `;
      header.addEventListener("click", () => {
        const isOpen = group.dataset.open === "true";
        group.dataset.open = isOpen ? "false" : "true";
      });
      group.appendChild(header);

      const itemsDiv = document.createElement("div");
      itemsDiv.className = "scenario-category-items";

      for (const scenario of scenarios) {
        const label = document.createElement("label");
        label.className = "scenario-check-item";
        label.innerHTML = `
          <input type="checkbox" value="${escapeAttribute(scenario.scenario_id)}" ${previouslyChecked.has(scenario.scenario_id) ? "checked" : ""}>
          <span class="check-label">${escapeHtml(scenario.title)}</span>
          <span class="check-id">${escapeHtml(scenario.scenario_id)}</span>
        `;
        itemsDiv.appendChild(label);
      }

      group.appendChild(itemsDiv);
      els.scenarioChecklist.appendChild(group);
    }

    els.scenarioChecklist.addEventListener("change", updateScenarioHint);
    updateScenarioHint();
    renderTesting();
  }
}

async function startCall() {
  els.startCall.disabled = true;
  if (els.testingStartCall) {
    els.testingStartCall.disabled = true;
  }
  els.statusNote.textContent = "Preparing test call...";
  try {
    const body = {};
    const personaId = getSelectedPersonaId();
    const scenarioIds = getSelectedScenarioIds();
    if (personaId) body.persona_id = personaId;
    if (scenarioIds) body.scenario_ids = scenarioIds;

    const response = await fetch("/api/testing/start-call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "call request failed");
    }
    els.statusNote.textContent = `Queued ${payload.persona_name || "test persona"} with ${(payload.scenario_ids || []).length} scenarios.`;
    setActiveTab("live");
    await loadSnapshot();
    await loadTestingReport();
  } catch (error) {
    els.statusNote.textContent = `Call failed: ${error.message}`;
  } finally {
    els.startCall.disabled = false;
    if (els.testingStartCall) {
      els.testingStartCall.disabled = false;
    }
  }
}

async function resetTestingCampaign() {
  if (!els.testingReset) {
    return;
  }

  els.testingReset.disabled = true;
  els.statusNote.textContent = "Resetting scenario progress...";
  try {
    const response = await fetch("/api/testing/reset", { method: "POST" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "reset failed");
    }
    applyTestingReport(payload.testing || state.testing);
    renderTesting();
    els.statusNote.textContent = "Scenario progress reset. Historical runs and recordings are still available.";
  } catch (error) {
    els.statusNote.textContent = `Reset failed: ${error.message}`;
  } finally {
    els.testingReset.disabled = false;
  }
}

async function transitionScenario(callSid, mode, targetScenarioId = null) {
  if (!callSid || !mode) return;
  els.statusNote.textContent = mode === "next"
    ? "Queuing next scenario..."
    : "Queuing selected scenario...";

  const response = await fetch(`/api/testing/active-runs/${encodeURIComponent(callSid)}/scenario-transition`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode,
      target_scenario_id: targetScenarioId,
      reason: "operator_dashboard",
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "scenario transition failed");
  }
  els.statusNote.textContent = payload.transition_queued
    ? "Scenario transition queued after the representative finishes speaking."
    : "Scenario state updated. Live bridge was not available to nudge Gemini.";
  await loadSnapshot();
  await loadTestingReport();
}

async function hangupCall() {
  const call = currentLiveCall();
  if (!call || !call.call_sid) {
    return;
  }

  els.hangupCall.disabled = true;
  els.statusNote.textContent = "Ending call...";
  try {
    const response = await fetch(`/api/calls/${encodeURIComponent(call.call_sid)}/hangup`, {
      method: "POST",
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "hangup failed");
    }
    els.statusNote.textContent = `Call ended: ${payload.call_sid}`;
    await loadSnapshot();
  } catch (error) {
    els.statusNote.textContent = `Hang up failed: ${error.message}`;
  } finally {
    els.hangupCall.disabled = !currentLiveCall();
  }
}

function connectDashboardSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  state.socket = new WebSocket(`${protocol}://${window.location.host}/ws/dashboard`);
  setConnectionState("connecting", "");

  state.socket.addEventListener("open", () => {
    setConnectionState("live", "live");
    els.statusNote.textContent = "Connected.";
  });

  state.socket.addEventListener("message", (message) => {
    const payload = JSON.parse(message.data);
    if (payload.type === "snapshot") {
      state.events = payload.events || [];
      hydrateTranscriptMessages(payload.transcript_messages || []);
      render();
      return;
    }

    if (payload.type !== "event" || !payload.event) {
      return;
    }

    state.events.push(payload.event);
    state.events = state.events.slice(-500);

    const name = eventName(payload.event);
    if (name === "transcript_reset") {
      clearTranscript();
    }

    const transcriptMessage = payload.event?.payload?.transcript_message;
    if (transcriptMessage) {
      applyTranscriptMessage(
        transcriptMessage,
        payload.event.payload.transcript_replaces_id || null,
      );
    }

    if (snapshotRelevantEvents.has(name)) {
      scheduleSnapshotRefresh();
    }
    if (recordingRelevantEvents.has(name)) {
      scheduleRecordingsRefresh();
      scheduleTestingRefresh();
    }
    if (testingRelevantEvents.has(name)) {
      scheduleTestingRefresh();
    }

    els.metricEventCount.textContent = String(state.events.length);
    renderLogs();
  });

  state.socket.addEventListener("close", () => {
    setConnectionState("offline", "off");
    els.statusNote.textContent = "Disconnected. Retrying...";
    window.setTimeout(connectDashboardSocket, 1500);
  });

  state.socket.addEventListener("error", () => {
    setConnectionState("error", "off");
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function escapeAttribute(value) {
  return String(value).replaceAll('"', "&quot;");
}

function normalizeTranscriptMessage(message) {
  return {
    id: String(message.id || ""),
    callSid: String(message.call_sid || message.callSid || "call"),
    speaker: message.speaker === "user" ? "user" : "agent",
    text: String(message.text || ""),
    committed: Boolean(message.committed),
    updatedAt: message.updated_at || message.updatedAt || new Date().toISOString(),
    sequence: Number(message.sequence || 0),
  };
}

function transcriptSpeakerLabel(speaker) {
  return speaker === "agent" ? "Gemini" : "Caller";
}

function isTranscriptPinned() {
  return (els.transcriptList.scrollHeight - els.transcriptList.scrollTop - els.transcriptList.clientHeight) < 48;
}

function scrollTranscriptToBottom() {
  els.transcriptList.scrollTop = els.transcriptList.scrollHeight;
}

function findTranscriptNode(messageId) {
  return Array.from(els.transcriptList.children).find((node) => node.dataset?.messageId === messageId) || null;
}

function ensureTranscriptEmptyState() {
  if (state.transcriptMessageMap.size) return;
  els.transcriptList.innerHTML = '<div class="empty">Transcript will appear here.</div>';
}

function clearTranscriptEmptyState() {
  const emptyNode = els.transcriptList.querySelector(".empty");
  if (emptyNode) {
    emptyNode.remove();
  }
}

function clearTranscript() {
  state.transcriptMessageMap = new Map();
  els.transcriptList.innerHTML = "";
  ensureTranscriptEmptyState();
}

function renderTranscriptNode(row, message) {
  row.className = `chat-row ${message.speaker}`;
  row.dataset.messageId = message.id;
  row.dataset.sequence = String(message.sequence);
  row.innerHTML = `
    <div class="chat-bubble ${message.committed ? "" : "live"}">
      <div class="chat-meta">${escapeHtml(transcriptSpeakerLabel(message.speaker))} · ${escapeHtml(formatTime(message.updatedAt))}${message.committed ? "" : " · live"}</div>
      <div class="transcript-text">${escapeHtml(message.text)}</div>
    </div>
  `;
}

function insertTranscriptNode(row, sequence) {
  const nextNode = Array.from(els.transcriptList.children).find((node) => {
    const nodeSequence = Number(node.dataset?.sequence || Number.MAX_SAFE_INTEGER);
    return nodeSequence > sequence;
  });
  if (nextNode) {
    els.transcriptList.insertBefore(row, nextNode);
  } else {
    els.transcriptList.appendChild(row);
  }
}

function applyTranscriptMessage(rawMessage, replacesId = null) {
  const message = normalizeTranscriptMessage(rawMessage);
  if (!message.id || !message.text) return;

  const wasPinned = isTranscriptPinned();
  clearTranscriptEmptyState();

  let row = replacesId ? findTranscriptNode(replacesId) : null;
  if (row && replacesId && replacesId !== message.id) {
    state.transcriptMessageMap.delete(replacesId);
  }
  if (!row) {
    row = findTranscriptNode(message.id);
  }
  if (!row) {
    row = document.createElement("article");
    insertTranscriptNode(row, message.sequence);
  }

  renderTranscriptNode(row, message);
  state.transcriptMessageMap.set(message.id, message);

  if (wasPinned || message.committed) {
    scrollTranscriptToBottom();
  }
}

function hydrateTranscriptMessages(messages) {
  state.transcriptMessageMap = new Map();
  els.transcriptList.innerHTML = "";
  const ordered = (messages || [])
    .map(normalizeTranscriptMessage)
    .filter((message) => message.id && message.text)
    .sort((left, right) => left.sequence - right.sequence || left.updatedAt.localeCompare(right.updatedAt));

  if (!ordered.length) {
    ensureTranscriptEmptyState();
    return;
  }

  for (const message of ordered) {
    applyTranscriptMessage(message);
  }
  scrollTranscriptToBottom();
}

function scheduleSnapshotRefresh(delayMs = 350) {
  if (state.snapshotRefreshTimer) {
    window.clearTimeout(state.snapshotRefreshTimer);
  }
  state.snapshotRefreshTimer = window.setTimeout(() => {
    loadSnapshot().catch(() => {});
  }, delayMs);
}

function scheduleRecordingsRefresh(delayMs = 900) {
  if (state.recordingsRefreshTimer) {
    window.clearTimeout(state.recordingsRefreshTimer);
  }
  state.recordingsRefreshTimer = window.setTimeout(() => {
    loadRecordings().catch(() => {});
  }, delayMs);
}

function scheduleTestingRefresh(delayMs = 500) {
  if (state.testingRefreshTimer) {
    window.clearTimeout(state.testingRefreshTimer);
  }
  state.testingRefreshTimer = window.setTimeout(() => {
    loadTestingReport().catch(() => {});
    loadSnapshot().catch(() => {});
  }, delayMs);
}

function syncRecordingsPolling() {
  if (state.activeTab === "recordings") {
    if (!state.recordingsPollTimer) {
      state.recordingsPollTimer = window.setInterval(() => {
        loadRecordings().catch(() => {});
      }, 5000);
    }
    loadRecordings().catch(() => {});
    return;
  }

  if (state.recordingsPollTimer) {
    window.clearInterval(state.recordingsPollTimer);
    state.recordingsPollTimer = null;
  }
}

els.startCall.addEventListener("click", startCall);
if (els.testingStartCall) {
  els.testingStartCall.addEventListener("click", startCall);
}
if (els.testingReset) {
  els.testingReset.addEventListener("click", resetTestingCampaign);
}
if (els.scenarioSelectAll) {
  els.scenarioSelectAll.addEventListener("click", () => setAllScenarioCheckboxes(true));
}
if (els.scenarioSelectNone) {
  els.scenarioSelectNone.addEventListener("click", () => setAllScenarioCheckboxes(false));
}
els.hangupCall.addEventListener("click", hangupCall);
els.refresh.addEventListener("click", () => loadSnapshot().catch((error) => {
  els.statusNote.textContent = `Refresh failed: ${error.message}`;
}));
els.togglePackets.addEventListener("click", () => {
  state.hidePackets = !state.hidePackets;
  els.togglePackets.textContent = state.hidePackets ? "Show Packet Logs" : "Hide Packet Logs";
  renderLogs();
});
els.recordingList.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  const button = target.closest("[data-recording-favorite]");
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  toggleRecordingFavorite(button).catch((error) => {
    els.statusNote.textContent = `Favorite update failed: ${error.message}`;
  });
});
els.testingActiveRuns.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  const button = target.closest("[data-scenario-transition]");
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  const callSid = button.dataset.callSid || "";
  const mode = button.dataset.scenarioTransition || "";
  const select = document.querySelector(`[data-scenario-select="${CSS.escape(callSid)}"]`);
  const targetScenarioId = mode === "select" && select instanceof HTMLSelectElement ? select.value : null;
  button.disabled = true;
  transitionScenario(callSid, mode, targetScenarioId)
    .catch((error) => {
      els.statusNote.textContent = `Scenario transition failed: ${error.message}`;
    })
    .finally(() => {
      button.disabled = false;
    });
});
if (els.liveScenarioStrip) {
  els.liveScenarioStrip.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const button = target.closest("[data-scenario-transition]");
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    const callSid = button.dataset.callSid || "";
    const mode = button.dataset.scenarioTransition || "";
    const select = els.liveScenarioStrip.querySelector(`[data-scenario-select="${CSS.escape(callSid)}"]`);
    const targetScenarioId = mode === "select" && select instanceof HTMLSelectElement ? select.value : null;
    button.disabled = true;
    transitionScenario(callSid, mode, targetScenarioId)
      .catch((error) => {
        els.statusNote.textContent = `Scenario transition failed: ${error.message}`;
      })
      .finally(() => {
        button.disabled = false;
      });
  });
}

for (const button of els.tabButtons) {
  button.addEventListener("click", () => setActiveTab(button.dataset.tab));
}

loadSnapshot().catch((error) => {
  els.statusNote.textContent = `Snapshot failed: ${error.message}`;
});
loadTestingReport().catch(() => {});
loadSelectors().catch(() => {});
ensureTranscriptEmptyState();
setActiveTab("live");
connectDashboardSocket();
