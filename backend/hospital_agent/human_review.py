"""Human Review Service (spec §1, §3 human rows, §12.4-§12.5, §18.4; sub-project 5 design §5).

The staff's side of an escalation: the queue, the context a reviewer is shown, and the
decision. A decision is a WorkflowDecision row (§12.5) plus the human event that uses it;
the State Manager's WorkflowDecisionValid guard judges the row - this service never decides
validity itself, it only refuses input that cannot be a legal transition at all.

The shown context is bound to the decision by `shown_context_ref`, a hash of exactly what
was shown: a decision on a context that has changed since (a new audit row, a deleted
entry) is refused (design decision 3). The context never includes a tombstoned entry's
content, nor an upload the case did not accept (the Orchestrator's rule, D25).
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from . import data_log, repository
from .auth import CLINICAL_STAFF
from .case import ApprovalRecord, CaseRecord, ExecutionRecord
from .naming import Action, Component, EscalationKind, Event, State
from .session import CaseNotFound, EventRejected, SessionService
from .state_manager import CaseNotFound as _UnknownCase
from .state_manager import StateManager, TransitionResult

logger = logging.getLogger(__name__)

# §3 HUMAN_APPROVED rows, with the fields the REVIEWER must supply. (naming.RESUMABLE also
# lists PolicyReview's plan_hash + current_step; those are taken from the case, not asked for.)
RESUMABLE: dict[EscalationKind, tuple[str, ...]] = {
    EscalationKind.PATIENT_VERIFICATION_FAILED: ("verified_identity_ref",),
    EscalationKind.RETRY_EXHAUSTED: (),
    EscalationKind.POLICY_REVIEW: (),
    EscalationKind.Z3_COUNTEREXAMPLE: ("patient_deadline",),
    EscalationKind.PATIENT_SLA_EXPIRED: ("patient_deadline",),
}
DECISION_EVENT = {
    "approve": Event.HUMAN_APPROVED,
    "resolve": Event.HUMAN_RESOLVED_CASE,
    "reject": Event.HUMAN_REJECTED,
}
DEFAULT_APPROVAL_TTL = timedelta(hours=1)  # design decision 4: consumed at once by the event


class NotInReview(Exception):
    """The case is not in AwaitingHumanReview (API 409 "not_in_review")."""


class ContextChanged(Exception):
    """The reviewer decided on a context that is no longer current (API 409 "context_changed")."""


class DecisionRejected(Exception):
    """Invalid input, or the human event was blocked (API 409 {"detail": reason})."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AnswerRejected(DecisionRejected):
    """A clinical answer that cannot be given (API 409/403 {"detail": reason})."""


@dataclass(frozen=True)
class ReviewItem:
    case_id: str
    patient_id: str
    escalation_kind: str
    escalated_from_state: str | None
    reasons: list[str]
    allowed_decisions: list[str]
    required_fields: list[str]
    updated_at: datetime


@dataclass(frozen=True)
class ReviewContext:
    case_id: str
    patient_id: str
    state: str
    escalation_kind: str | None
    escalated_from_state: str | None
    reasons: list[str]
    data: list[dict]
    trace: list[dict]
    shown_context_ref: str


def allowed_decisions(kind: EscalationKind | None) -> list[str]:
    """approve only where a §3 HUMAN_APPROVED row exists; resolve and reject always."""
    return ["approve", "resolve", "reject"] if kind in RESUMABLE else ["resolve", "reject"]


class HumanReviewService:
    def __init__(self, state_manager: StateManager, session: SessionService, *,
                 wake: Callable[[], None] = lambda: None, approval_ttl: timedelta = DEFAULT_APPROVAL_TTL) -> None:
        self.sm = state_manager
        self.engine = state_manager.engine
        self.session = session
        self.wake = wake
        self.approval_ttl = approval_ttl

    # --- reading -------------------------------------------------------------------------

    def queue(self) -> list[ReviewItem]:
        """The cases waiting for a reviewer, oldest update first."""
        with self.engine.connect() as conn:
            cases = repository.list_cases(conn, State.AWAITING_HUMAN_REVIEW)
            traces = {case.case_id: repository.load_trace(conn, case.case_id) for case in cases}
        cases.sort(key=lambda case: (case.updated_at, case.case_id))
        return [
            ReviewItem(
                case_id=case.case_id,
                patient_id=case.patient_id,
                # A case in AwaitingHumanReview always carries a kind (EscalationCoordinator.
                # signal() sets it in the same transition that enters this state), so the ""
                # fallback here is defensive only, never actually taken.
                escalation_kind=case.escalation_kind.value if case.escalation_kind else "",
                escalated_from_state=case.escalated_from_state.value if case.escalated_from_state else None,
                reasons=_escalation_reasons(traces[case.case_id]),
                allowed_decisions=allowed_decisions(case.escalation_kind),
                required_fields=list(RESUMABLE.get(case.escalation_kind, ())),
                updated_at=case.updated_at,
            )
            for case in cases
        ]

    def context(self, case_id: str) -> ReviewContext:
        case = self._load(case_id)
        with self.engine.connect() as conn:
            trace = repository.load_trace(conn, case_id)
            accepted = data_log.accepted_uploads(trace)
            entries = [entry for kind in data_log.DataKind for entry in data_log.entries(conn, case_id, kind)]
        entries.sort(key=lambda entry: (entry.created_at, entry.entry_id))
        data = [
            {"entry_id": e.entry_id, "kind": e.kind.value, "content": e.content, "content_hash": e.content_hash,
             "created_at": e.created_at}
            for e in entries
            if e.content is not None
            and (e.kind is not data_log.DataKind.UPLOADED_DOCUMENT or e.content_hash in accepted)
        ]
        rows = [
            {"audit_id": r.audit_id, "record_type": r.record_type, "event": r.event, "state_before": r.state_before,
             "state_after": r.state_after, "action": r.action, "policy_result": r.policy_result,
             "policy_reasons": list(r.policy_reasons), "recorded_at": r.recorded_at}
            for r in trace
        ]
        shown = {
            "case_id": case.case_id,
            "patient_id": case.patient_id,
            "state": case.state.value,
            "escalation_kind": case.escalation_kind.value if case.escalation_kind else None,
            "escalated_from_state": case.escalated_from_state.value if case.escalated_from_state else None,
            "reasons": _escalation_reasons(trace) if case.state is State.AWAITING_HUMAN_REVIEW else [],
            "data": data,
            "trace": rows,
        }
        return ReviewContext(**shown, shown_context_ref=_context_ref(shown))

    # --- deciding --------------------------------------------------------------------------

    def decide(self, *, reviewer_id: str, reviewer_role: str, case_id: str, decision: str, reason: str,
               shown_context_ref: str, verified_identity_ref: str | None = None,
               patient_deadline: datetime | None = None) -> TransitionResult:
        """Record the reviewer's decision and apply its human event (§3, §12.5).

        Refuses before anything is written: an unknown or not-escalated case, invalid input,
        a context that has changed, or a blocked event (no approval row is left behind in
        the first three). Once the event commits the decision is never reported as an error -
        a case that cannot then be revalidated simply stays in Received for the staff.
        """
        case = self._load(case_id)
        if case.state is not State.AWAITING_HUMAN_REVIEW:
            raise NotInReview(case_id)

        if decision not in DECISION_EVENT:
            raise DecisionRejected("invalid_decision")
        if not (reason or "").strip():
            raise DecisionRejected("reason_required")
        given = {"verified_identity_ref": verified_identity_ref, "patient_deadline": patient_deadline}
        if decision == "approve":
            if case.escalation_kind not in RESUMABLE:
                raise DecisionRejected("decision_not_allowed")
            for name in RESUMABLE[case.escalation_kind]:
                value = given[name]
                if value is None or (isinstance(value, str) and not value.strip()):
                    raise DecisionRejected(f"{name}_required")

        if shown_context_ref != self.context(case_id).shown_context_ref:
            raise ContextChanged(case_id)

        approval_id = self._grant(case, reviewer_id, reviewer_role, decision, reason, shown_context_ref,
                                  verified_identity_ref, patient_deadline)
        result = self.sm.apply(case_id, DECISION_EVENT[decision], {"approval_id": approval_id}, Component.EXTERNAL)
        if not result.committed:
            raise DecisionRejected(result.reason or "blocked")

        if (decision == "approve" and case.escalation_kind is EscalationKind.PATIENT_VERIFICATION_FAILED
                and result.state_after is State.RECEIVED):
            # Design decision 7: identity is now established - the request goes on from the
            # text the patient already submitted. If it cannot (the text was deleted, or the
            # event is blocked), the committed decision still stands: the case stays in
            # Received and the staff see it in the monitor.
            try:
                self.session.revalidate(case_id)
            except EventRejected as rejected:
                logger.info("case not revalidated after an identity approval: %s", rejected.reason)
        self.wake()
        return result

    def answer(self, *, reviewer_id: str, reviewer_role: str, case_id: str, answer: str,
               reason: str, shown_context_ref: str) -> TransitionResult:
        """§5 AnswerClinicalQuestion: record a clinical answer, authorise it and close the case.

        The Human Review Service is this action's actor (§5), not the Tool Executor: there is
        no external call, so there is no TOOL_EXECUTION_STARTED row and T6 does not apply. What
        makes the answer deliverable is the ContentApproval, which the State Manager re-checks
        and consumes inside the transition's own transaction (§6.3).
        """
        case = self._load(case_id)
        if case.state is not State.AWAITING_HUMAN_REVIEW:
            raise NotInReview(case_id)
        if reviewer_role != CLINICAL_STAFF:
            raise AnswerRejected("clinical_staff_only")          # §12.4
        if case.escalation_kind is not EscalationKind.MEDICAL_QUESTION:
            raise AnswerRejected("decision_not_allowed")
        text = (answer or "").strip()
        if not text:
            raise AnswerRejected("answer_required")
        if not (reason or "").strip():
            raise AnswerRejected("reason_required")
        if shown_context_ref != self.context(case_id).shown_context_ref:
            raise ContextChanged(case_id)

        now = self.sm.clock()
        with self.engine.begin() as conn:
            entry = data_log.record(conn, case.case_id, case.patient_id,
                                    data_log.DataKind.OUTGOING_MESSAGE, text, now)
            execution_id = f"EXEC-{uuid.uuid4().hex[:12]}"
            repository.insert_execution(conn, ExecutionRecord(
                execution_id=execution_id,
                case_id=case.case_id,
                patient_id=case.patient_id,
                action=Action.ANSWER_CLINICAL_QUESTION.value,
                step=case.current_step or 0,
                retry_cycle=case.retry_cycle,
                attempt_number=0,
                idempotency_key=f"{case.case_id}:answer:{execution_id}",
                # Decision 4: final at once. There is no external call to wait for, and restart
                # recovery escalates any execution left in 'started' as ExecutionUnknown.
                status="succeeded",
                state_version=case.state_version,
                content_hash=entry.content_hash,
                medical_content_flag=True,
            ))
        content_id = self._grant_content_approval(case, reviewer_id, reviewer_role, reason,
                                                  shown_context_ref, execution_id, entry.content_hash)
        workflow_id = self._grant(case, reviewer_id, reviewer_role, "resolve", reason,
                                  shown_context_ref, None, None)
        try:
            result = self.sm.apply(case_id, Event.HUMAN_RESOLVED_CASE,
                                   {"approval_id": workflow_id, "content_approval_id": content_id},
                                   Component.EXTERNAL)
        except Exception:
            # Fail closed, the raising exit: sm.apply() can also raise instead of returning
            # a not-committed result - ReprocessLimitExceeded, or the Temporal Monitor being
            # unavailable, which by design lets its exception through uncaught (see CLAUDE.md,
            # "If the Monitor is unavailable, nothing commits"). Either way the text just
            # recorded above was never authorised to be shown, so it must not stay readable.
            self._tombstone_unauthorised(entry.entry_id)
            raise
        if not result.committed:
            # Fail closed: the text is medical content that was never authorised to be shown.
            self._tombstone_unauthorised(entry.entry_id)
            raise AnswerRejected(result.reason or "blocked")
        self.wake()
        return result

    def _tombstone_unauthorised(self, entry_id: str) -> None:
        with self.engine.begin() as conn:
            data_log.tombstone(conn, entry_id, self.sm.clock())

    def _grant_content_approval(self, case: CaseRecord, reviewer_id: str, reviewer_role: str,
                                reason: str, shown_context_ref: str, execution_id: str,
                                content_hash: str) -> str:
        """The §12.4 ContentApproval: bound to execution_id + action + content_hash."""
        granted_at = self.sm.clock()
        approval = ApprovalRecord(
            approval_id=f"APPR-{uuid.uuid4().hex[:12]}",
            approval_type="ContentApproval",
            case_id=case.case_id,
            patient_id=case.patient_id,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            decision="approve",
            reason=reason,
            shown_context_ref=shown_context_ref,
            granted_at=granted_at,
            valid_until=granted_at + self.approval_ttl,
            execution_id=execution_id,
            action=Action.ANSWER_CLINICAL_QUESTION.value,
            content_hash=content_hash,
        )
        with self.engine.begin() as conn:
            repository.insert_approval(conn, approval)
        return approval.approval_id

    def tombstone(self, case_id: str, entry_id: str) -> bool:
        """§18.4: clear one of this case's Data Log entries. Audit is never changed."""
        with self.engine.begin() as conn:
            if not any(entry.entry_id == entry_id
                       for kind in data_log.DataKind for entry in data_log.entries(conn, case_id, kind)):
                return False
            return data_log.tombstone(conn, entry_id, self.sm.clock()) == 1

    # --- helpers ---------------------------------------------------------------------------

    def _grant(self, case: CaseRecord, reviewer_id: str, reviewer_role: str, decision: str, reason: str,
               shown_context_ref: str, verified_identity_ref: str | None,
               patient_deadline: datetime | None) -> str:
        """Insert the WorkflowDecision (§12.5). Whether it is valid is the guard's to judge.

        This insert is its own transaction, separate from the one that applies the human
        event below. If that later transaction is blocked (or never runs), the row is left
        behind unconsumed until it expires at `valid_until`. That is safe: no API path
        accepts a raw `approval_id` from a caller, so an orphaned row can only be reached
        again through `decide()`, which re-derives it from a fresh `context()` call and the
        guard re-checks everything (`shown_context_ref`, the escalation kind, the required
        fields) before it could ever be consumed.
        """
        granted_at = self.sm.clock()
        policy_review = case.escalation_kind is EscalationKind.POLICY_REVIEW
        approval = ApprovalRecord(
            approval_id=f"APPR-{uuid.uuid4().hex[:12]}",
            approval_type="WorkflowDecision",
            case_id=case.case_id,
            patient_id=case.patient_id,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            decision=decision,
            reason=reason,
            shown_context_ref=shown_context_ref,
            granted_at=granted_at,
            valid_until=granted_at + self.approval_ttl,
            escalation_kind=case.escalation_kind.value if case.escalation_kind else None,
            plan_hash=case.plan_hash if policy_review else None,
            current_step=case.current_step if policy_review else None,
            verified_identity_ref=verified_identity_ref,
            patient_deadline=patient_deadline,
        )
        with self.engine.begin() as conn:
            repository.insert_approval(conn, approval)
        return approval.approval_id

    def _load(self, case_id: str) -> CaseRecord:
        try:
            return self.sm.load(case_id)
        except _UnknownCase:
            raise CaseNotFound(case_id) from None


def _escalation_reasons(trace: list[repository.AuditEntry]) -> list[str]:
    """policy_reasons of the row that escalated the case: its latest committed move into review."""
    for row in reversed(trace):
        if row.record_type == "Transition" and row.state_after == State.AWAITING_HUMAN_REVIEW.value:
            return list(row.policy_reasons)
    return []


def _context_ref(shown: dict[str, Any]) -> str:
    canonical = json.dumps(shown, sort_keys=True, separators=(",", ":"), default=_jsonable, ensure_ascii=False)
    return "ctx-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")
