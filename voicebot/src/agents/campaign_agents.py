"""Campaign planning and scenario orchestration agents."""

from __future__ import annotations

from typing import Any

from voicebot.src.agents.base import BaseAgent


class CampaignSelectorAgent(BaseAgent):
    """Owns deterministic persona/scenario planning through the campaign manager."""

    name = "campaign_selector"

    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def list_personas(self) -> list[dict[str, Any]]:
        return self.manager.list_personas()

    def list_scenarios(self) -> list[dict[str, Any]]:
        return self.manager.list_scenarios()

    def preview_next_call(self, **kwargs: Any) -> Any:
        return self.manager.preview_next_call(**kwargs)

    def plan_next_call(self, **kwargs: Any) -> Any:
        return self.manager.plan_next_call(**kwargs)

    def reset_campaign(self) -> None:
        self.manager.reset_campaign()


class ScenarioOrchestratorAgent(BaseAgent):
    """Owns live scenario state and manual transition requests."""

    name = "scenario_orchestrator"

    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def transition_scenario(
        self,
        *,
        call_sid: str,
        mode: str,
        target_scenario_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return self.manager.transition_active_scenario(
            call_sid=call_sid,
            mode=mode,
            target_scenario_id=target_scenario_id,
            reason=reason,
        )
