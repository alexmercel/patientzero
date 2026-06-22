from __future__ import annotations

import pytest

from voicebot.src.agents.base import AgentEvent, BaseAgent
from voicebot.src.agents.runtime import AgentRuntime


class ProbeAgent(BaseAgent):
    name = "probe"

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_agent_runtime_lifecycle_and_event_delivery() -> None:
    runtime = AgentRuntime()
    agent = ProbeAgent()
    seen: list[AgentEvent] = []
    runtime.register(agent)
    runtime.subscribe("*", lambda event: seen.append(event))

    await runtime.start()
    event = await runtime.publish(
        AgentEvent(
            type="testing.example",
            source="probe",
            call_sid="CA_AGENT",
            payload={"items": {"alpha", "beta"}},
        )
    )
    await runtime.stop()

    assert agent.started is True
    assert agent.stopped is True
    assert seen == [event]
    assert sorted(event.payload["items"]) == ["alpha", "beta"]
    assert runtime.status()["agent_count"] == 1
