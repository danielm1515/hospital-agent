"""Planner Service (spec §1: "מפרק לצעדים, מציע צעד ומאמת Schema").

plan() decomposes the classified request into ordered_steps; propose() proposes the next
step (§2.2 ACTION_PROPOSED: "Planner הציע צעד. הצעה בלבד"). Both are the Planner call of
§18.5 with its own prompt and schema. Nothing the Planner returns is trusted: PlanComplete
and the plan's shape are checked on PLAN_CREATED, InPlan and PlanIntact on
ACTION_PROPOSED, and OPA again on the Policy decision (§3.1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .provider import LLMProvider, ask
from .schemas import PLAN_SCHEMA, PROPOSAL_SCHEMA, Call


@dataclass(frozen=True)
class Plan:
    plan_complete: bool
    ordered_steps: list[dict[str, Any]]


class Planner:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def plan(self, intent: str, request_text: str) -> Plan:
        answer = ask(self.provider, Call.PLANNER, {"task": "plan", "intent": intent, "request_text": request_text},
                     PLAN_SCHEMA)
        return Plan(answer["plan_complete"], answer["ordered_steps"])

    def propose(self, ordered_steps: list[dict[str, Any]], current_step: int, last_event: str) -> dict[str, Any]:
        """The proposed_action payload of ACTION_PROPOSED. The request text is not shown."""
        user_input = {"task": "propose", "ordered_steps": ordered_steps, "current_step": current_step,
                      "last_event": last_event}
        answer = ask(self.provider, Call.PLANNER, user_input, PROPOSAL_SCHEMA)
        return {"action": answer["action"], "from_step": answer["from_step"]}
