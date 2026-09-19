"""Policy Service - one decision per proposed action (spec §1, §7-§10, §14; Policy design §6).

decide() evaluates OPA (the spec's Rego, run by the real binary) and Prolog (rules.pl,
in a fresh isolated engine) on the same trusted state, and folds them:

    OPA Deny, or Prolog blocks          -> Deny   (reasons from both; §14: disagreement denies)
    OPA RequireHumanReview              -> RequireHumanReview
    OPA Allow and Prolog allows         -> Allow
    an engine is unavailable            -> Deny policy_engine_unavailable

decision_event() turns the decision into POLICY_ALLOWED / POLICY_DENIED /
POLICY_HUMAN_REVIEW_REQUIRED, emitted with source=PolicyService.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any

from sqlalchemy.engine import Engine

from .. import repository
from ..case import MAX_ATTEMPTS, ApprovalRecord, CaseRecord
from ..naming import Action, Component, Event, to_prolog
from ..state_manager import StateManager, TransitionResult
from . import opa_runner
from .approvals import content_approval_valid
from .opa_runner import OpaDecision
from .prolog import RULES_FILE, Prolog, parse_program

AUTOMATION_ACTOR = "patient_agent"
DECISION_EVENT = {
    "Allow": Event.POLICY_ALLOWED,
    "Deny": Event.POLICY_DENIED,
    "RequireHumanReview": Event.POLICY_HUMAN_REVIEW_REQUIRED,
}


@dataclass(frozen=True)
class ProposedAction:
    action: str
    from_step: int | None
    target_system: str
    patient_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutgoingMessage:
    """Set by the Response Evaluator only (§6.5) - never by the Planner or the LLM."""

    evaluated: bool
    medical_content_flag: bool
    content_hash: str


@dataclass(frozen=True)
class InstructionSource:
    source_id: str
    version: str


@dataclass(frozen=True)
class PolicyRequest:
    """What trusted components supply for one attempt (§8: loaded from trusted services)."""

    execution_id: str
    proposed_action: ProposedAction
    outgoing_message: OutgoingMessage | None = None
    approval_id: str | None = None
    instruction_source: InstructionSource | None = None
    patient_verification_status: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    result: str  # Allow | Deny | RequireHumanReview
    reasons: tuple[str, ...]
    action: str
    execution_id: str
    decision_token: str
    content_hash: str | None = None
    content_approval_valid: bool = False
    medical_content_flag: bool = False
    policy_review_override_id: str | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _rfc3339(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _approval_json(approval: ApprovalRecord | None) -> dict[str, Any] | None:
    if approval is None:
        return None
    data = {k: _rfc3339(v) if isinstance(v, datetime) else v for k, v in approval.__dict__.items()}
    # The approvals table has no escalated_from_state column (§18.2); a PolicyReview
    # escalation always comes from Planning (§3 row POLICY_HUMAN_REVIEW_REQUIRED).
    if approval.escalation_kind == "PolicyReview":
        data["escalated_from_state"] = "Planning"
    return data


def build_opa_input(
    case: CaseRecord,
    request: PolicyRequest,
    override: ApprovalRecord | None,
    approval: ApprovalRecord | None = None,
) -> dict[str, Any]:
    proposal, message, source = request.proposed_action, request.outgoing_message, request.instruction_source
    return {
        "case_id": case.case_id,
        "patient_id": case.patient_id,
        "execution_id": request.execution_id,
        "identity_verified": case.identity_verified,
        "safety_level": case.safety_level.value if case.safety_level else None,
        "intent": case.intent,
        "execution": {"attempt_count": case.attempt_count, "max_attempts": MAX_ATTEMPTS},
        "plan": {"current_step": case.current_step, "plan_hash": case.plan_hash, "ordered_steps": case.ordered_steps},
        "proposed_action": {
            "action": proposal.action,
            "from_step": proposal.from_step,
            "target_system": proposal.target_system,
            "parameters": {"patient_fields": list(proposal.patient_fields)},
        },
        "outgoing_message": None if message is None else {
            "evaluated": message.evaluated,
            "medical_content_flag": message.medical_content_flag,
            "content_hash": message.content_hash,
        },
        "approval": _approval_json(approval),
        "policy_review_override": _approval_json(override),
        "instruction_source": None if source is None else {"source_id": source.source_id, "version": source.version},
        "patient": {"verification_status": request.patient_verification_status},
    }


@cache
def _rule_clauses() -> tuple:
    return tuple(parse_program(RULES_FILE.read_text(encoding="utf-8")))


def _atom(value: str) -> str:
    return "'" + value.replace("'", "\\'") + "'"


def prolog_verdict(case: CaseRecord, request: PolicyRequest, approval_ok: bool) -> tuple[bool, str]:
    """explain/4 of rules.pl for the automation actor, over this request's facts only."""
    action = request.proposed_action.action
    if action not in {a.value for a in Action}:
        return False, "action_not_supported"
    engine = Prolog()
    engine.clauses = list(_rule_clauses())
    c, p, e = _atom(case.case_id), _atom(case.patient_id), _atom(request.execution_id)
    if case.identity_verified:
        engine.assertz(f"case_identity_verified({c})")
    engine.assertz(f"case_patient({c}, {p})")
    if case.current_action is not None:
        engine.assertz(f"case_step({c}, {to_prolog(case.current_action)})")
    engine.assertz(f"case_execution({c}, {e})")
    engine.assertz(f"current_execution({e})")
    engine.assertz(f"current_patient({p})")
    message = request.outgoing_message
    if message is not None:
        engine.assertz(f"current_content_hash({_atom(message.content_hash)})")
        if message.evaluated:
            engine.assertz("outgoing_message_evaluated")
        engine.assertz(f"outgoing_medical_content({'true' if message.medical_content_flag else 'false'})")
        if approval_ok:
            engine.assertz(f"content_approval_valid({e}, {p}, {to_prolog(action)}, {_atom(message.content_hash)})")
    [answer] = engine.solve(f"explain({AUTOMATION_ACTOR}, {to_prolog(action)}, {c}, R)", limit=1)
    return answer["R"] == "allowed", answer["R"]


class PolicyService:
    def __init__(
        self,
        engine: Engine,
        *,
        opa: Callable[[Mapping[str, Any]], OpaDecision] = opa_runner.evaluate,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.engine, self.opa, self.clock = engine, opa, clock

    def decide(self, case: CaseRecord, request: PolicyRequest) -> PolicyDecision:
        now = self.clock()
        message = request.outgoing_message
        try:
            with self.engine.connect() as conn:
                override = repository.open_policy_review_override(
                    conn, case.case_id, case.plan_hash, case.current_step
                )
                # F2: the caller supplies only an id - the Policy Service loads the trusted
                # record itself. An id that is not on the approvals table behaves exactly like
                # no approval.
                approval = repository.load_approval(conn, request.approval_id) if request.approval_id else None
            approval_ok = message is not None and content_approval_valid(
                approval,
                case_id=case.case_id,
                patient_id=case.patient_id,
                execution_id=request.execution_id,
                action=request.proposed_action.action,
                content_hash=message.content_hash,
                now=now,
            )
            opa = self.opa(build_opa_input(case, request, override, approval=approval))
        except Exception:  # noqa: BLE001 - M2: any failure loading input for OPA fails closed (§14)
            override, approval_ok, opa = None, False, opa_runner.UNAVAILABLE
        try:
            prolog_allowed, explanation = prolog_verdict(case, request, approval_ok)
        except Exception:  # noqa: BLE001 - any engine failure fails closed (§14)
            prolog_allowed, explanation = False, "policy_engine_unavailable"

        reasons = list(opa.reasons) if opa.result == "Deny" else []
        if not prolog_allowed:
            reasons.append(f"prolog:{explanation}")
        if opa.result == "Deny" or not prolog_allowed:
            result = "Deny"
        elif opa.result == "RequireHumanReview":
            result, reasons = "RequireHumanReview", list(opa.reasons)
        else:
            result = "Allow"
        return PolicyDecision(
            result=result,
            reasons=tuple(reasons),
            action=request.proposed_action.action,
            execution_id=request.execution_id,
            decision_token=uuid.uuid4().hex,
            content_hash=None if message is None else message.content_hash,
            content_approval_valid=approval_ok,
            medical_content_flag=bool(message and message.medical_content_flag),
            policy_review_override_id=None if override is None else override.approval_id,
        )

    def apply(self, state_manager: StateManager, case_id: str, request: PolicyRequest) -> TransitionResult:
        decision = self.decide(state_manager.load(case_id), request)
        event, payload = decision_event(decision)
        return state_manager.apply(case_id, event, payload, Component.POLICY_SERVICE)


def decision_event(decision: PolicyDecision) -> tuple[Event, dict[str, Any]]:
    payload: dict[str, Any] = {
        "action": decision.action,
        "policy_result": decision.result,
        "policy_reasons": list(decision.reasons),
        "decision_token": decision.decision_token,
        "execution_id": decision.execution_id,
        "evidence": {
            "ContentApprovalValid": decision.content_approval_valid,
            "medical_content_flag": decision.medical_content_flag,
        },
    }
    if decision.content_hash is not None:
        payload["content_hash"] = decision.content_hash
    if decision.policy_review_override_id is not None:
        payload["policy_review_override_id"] = decision.policy_review_override_id
    return DECISION_EVENT[decision.result], payload
