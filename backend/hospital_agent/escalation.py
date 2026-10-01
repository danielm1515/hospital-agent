"""Escalation Coordinator - the only emitter of HUMAN_REVIEW_REQUIRED (spec §3.1, §13.2).

Internal components never emit HUMAN_REVIEW_REQUIRED themselves: they send a signal
here. The signal becomes the canonical event only if it comes from an authorized
internal component, names a known escalation kind, and names the current State as
its origin; the §3 row then checks the kind against that State's allowlist.
Anything else is Blocked: invalid_escalation_reason.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from .guards import INVALID_ESCALATION_REASON
from .naming import ESCALATION_SIGNAL_SOURCES, Component, EscalationKind, Event, State

if TYPE_CHECKING:
    from .state_manager import ExecutionOutcome, StateManager, TransitionResult


class EscalationCoordinator:
    def __init__(self, state_manager: StateManager) -> None:
        self._state_manager = state_manager

    def signal(
        self,
        case_id: str,
        kind: EscalationKind | str,
        from_state: State | str,
        source: Component,
        reasons: Sequence[str] = (),
        execution_outcome: ExecutionOutcome | None = None,
        classification: Mapping[str, str] | None = None,
    ) -> TransitionResult:
        """`reasons` (e.g. a Z3 counterexample) are kept in the escalation row's policy_reasons.

        `execution_outcome`: the Tool Executor's call this escalation reports on (a
        non-idempotent failure, or an unknown outcome after a restart) - recorded in the
        same transaction as the escalation.
        """
        valid_kind = kind in {k.value for k in EscalationKind}
        valid_state = from_state in {s.value for s in State}
        if source not in ESCALATION_SIGNAL_SOURCES or not valid_kind or not valid_state:
            return self._state_manager.record_blocked(case_id, Event.HUMAN_REVIEW_REQUIRED, INVALID_ESCALATION_REASON)
        payload = {"escalation_kind": str(kind), "escalated_from_state": str(from_state),
                   "policy_reasons": list(reasons)}
        if classification:  # a SafetyEscalation from Classifying: what it was classified as (finding 02)
            payload.update({key: classification[key] for key in ("intent", "safety_level") if key in classification})
        return self._state_manager.apply(case_id, Event.HUMAN_REVIEW_REQUIRED, payload, Component.ESCALATION_COORDINATOR,
                                         execution_outcome=execution_outcome)
