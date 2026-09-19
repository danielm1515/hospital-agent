"""State Manager - the only writer of State (spec §3, §12, §18.2; design §7).

apply() runs one transaction per event:

    read cases -> ownership check -> resolve the §3 row -> Temporal Monitor
    -> UPDATE cases WHERE state_version + INSERT audit_log (+ consume approval) -> COMMIT

A blocked event writes one Blocked audit row and changes nothing else. A stale
state_version rolls the whole transaction back and the event is re-processed
against the fresh row, at most MAX_REPROCESS times (design §12.2), then fails closed.

For the Tool Executor (Execution design §3) it also:
  - writes the executions 'intent' row with POLICY_ALLOWED (Effect.RECORD_EXECUTION_INTENT);
  - start_execution(): re-verifies the decision, increments attempt_count and writes the
    TOOL_EXECUTION_STARTED / AUDIT_RECORDED pair, in one transaction;
  - apply(..., execution_outcome=...): records a call's outcome row in the same
    transaction as the event that follows it.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.engine import Connection, Engine

from . import repository
from .case import MAX_ATTEMPTS, CaseRecord, ExecutionRecord, new_case
from .escalation import EscalationCoordinator
from .execution.verify import verify_start
from .fsm import Effect, Resolution, apply_effects, resolve
from .guards import GuardContext, GuardPorts
from .naming import (
    EVENT_OWNER,
    NON_TRANSITION_EVENTS,
    POLICY_DECISION_EVENTS,
    Component,
    EscalationKind,
    Event,
    State,
    canonical_event,
)
from .repository import AuditEntry

MAX_REPROCESS = 3
# §12.2 rule_version of the transition table. wiring.build_state_manager() appends the
# version of the policy files (policy.rego, rules.pl, flows.dl).
RULE_VERSION = "transitions-v1"


class TraceMonitor(Protocol):
    """The Temporal Monitor port (§7). The real one arrives in sub-project 2."""

    def check(self, trace: Sequence[AuditEntry], candidate: AuditEntry) -> str | None:
        """Return the violated rule id (e.g. "T1"), or None. Raising means unavailable: no commit."""


class CaseNotFound(LookupError):
    pass


class NonTransitionEvent(ValueError):
    """TOOL_EXECUTION_STARTED / AUDIT_RECORDED are recorded by the Tool Executor (sub-project 3)."""


class ReprocessLimitExceeded(RuntimeError):
    """The case kept changing underneath this event. Nothing was committed."""


class _StaleVersion(Exception):
    pass


class ExecutionStateError(RuntimeError):
    """An execution outcome for a row that is not running - a replayed or duplicate finish."""


# §12.2: the audit record_type of each outcome (Execution design decision 4).
OUTCOME_RECORD_TYPES = {"succeeded": "ExecutionSucceeded", "failed": "ExecutionFailed", "unknown": "ExecutionUnknown"}
OUTCOME_VALUES = {"succeeded": "success", "failed": "failed", "unknown": "unknown"}


@dataclass(frozen=True)
class ExecutionOutcome:
    """The result of one external call, recorded with the event that follows it."""

    execution_id: str
    status: str  # succeeded | failed | unknown
    reason: str | None = None


@dataclass(frozen=True)
class StartResult:
    started: bool
    reason: str | None = None  # executor_reverification_failed | attempts_exhausted | temporal_violation:<rule>
    temporal_violation: str | None = None
    execution: ExecutionRecord | None = None


POLICY_EVIDENCE_KEYS = frozenset({"ContentApprovalValid", "medical_content_flag"})


def _policy_evidence(event: Event, payload: Mapping[str, Any]) -> dict[str, bool]:
    """Evidence the Policy Service attaches to its decision row (§6.1: HumanAuthorized is the
    ContentApprovalValid evidence kept on the POLICY_ALLOWED row). Only Policy decisions may
    carry it - the owner check has already proven they come from the Policy Service. M1: only
    these two named keys are accepted (an unknown key, however boolean, is dropped) - the
    caller must never be able to forge or override an unrelated guard's result."""
    if event not in POLICY_DECISION_EVENTS:
        return {}
    evidence = payload.get("evidence") or {}
    return {str(k): v for k, v in evidence.items() if k in POLICY_EVIDENCE_KEYS and isinstance(v, bool)}


@dataclass(frozen=True)
class TransitionResult:
    case_id: str | None  # None only when REQUEST_SUBMITTED itself was rejected
    committed: bool
    state_before: State | None
    state_after: State | None  # the case's State after this call
    reason: str | None = None  # why it was blocked
    audit_id: int | None = None
    temporal_violation: str | None = None
    escalation: TransitionResult | None = None  # the TemporalViolation escalation that followed


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_case_id() -> str:
    return f"CASE-{uuid.uuid4().hex[:12].upper()}"


class StateManager:
    def __init__(
        self,
        engine: Engine,
        monitor: TraceMonitor,
        ports: GuardPorts,
        *,
        clock: Callable[[], datetime] = _utcnow,
        new_case_id: Callable[[], str] = _new_case_id,
        rule_version: str = RULE_VERSION,
    ) -> None:
        self.engine = engine
        self.monitor = monitor
        self.ports = ports
        self.clock = clock
        self.new_case_id = new_case_id
        self.rule_version = rule_version
        self.escalation = EscalationCoordinator(self)

    # --- public API ----------------------------------------------------------------

    def apply(
        self,
        case_id: str | None,
        event: Event | str,
        payload: Mapping[str, Any] | None = None,
        source: Component = Component.EXTERNAL,
        *,
        execution_outcome: ExecutionOutcome | None = None,
    ) -> TransitionResult:
        """Apply one event. case_id is None only for REQUEST_SUBMITTED.

        execution_outcome: the call this event reports on; its outcome row is written in
        the same transaction, whether the event commits or is blocked.
        """
        event = canonical_event(event)
        if event in NON_TRANSITION_EVENTS:
            raise NonTransitionEvent(f"{event} is recorded by the Tool Executor, not applied as a transition")
        payload = dict(payload or {})
        for _ in range(MAX_REPROCESS + 1):
            try:
                result = self._apply_once(case_id, event, payload, source, execution_outcome)
            except _StaleVersion:
                continue
            if result.temporal_violation and result.case_id and event is not Event.HUMAN_REVIEW_REQUIRED:
                follow_up = self.escalation.signal(
                    result.case_id, EscalationKind.TEMPORAL_VIOLATION, result.state_after, Component.TEMPORAL_MONITOR
                )
                result = replace(result, escalation=follow_up)
            return result
        raise ReprocessLimitExceeded(f"{event} on {case_id}: state_version kept changing")

    def record_blocked(self, case_id: str, event: Event, reason: str) -> TransitionResult:
        """Write a Blocked audit row without attempting a transition.

        Used by the Escalation Coordinator for an invalid signal, whose verdict does not
        depend on State: it loads `case` itself, in this same transaction, so there is no
        earlier possibly-stale read to reconcile - check_version is skipped (F4).
        """
        with self.engine.begin() as conn:
            case = repository.load_case(conn, case_id)
            if case is None:
                raise CaseNotFound(case_id)
            return self._block(conn, case, event, reason, self.clock(), check_version=False)

    def load(self, case_id: str) -> CaseRecord:
        with self.engine.connect() as conn:
            case = repository.load_case(conn, case_id)
        if case is None:
            raise CaseNotFound(case_id)
        return case

    # --- one attempt, one transaction ------------------------------------------------

    def _apply_once(
        self,
        case_id: str | None,
        event: Event,
        payload: dict[str, Any],
        source: Component,
        outcome: ExecutionOutcome | None = None,
    ) -> TransitionResult:
        now = self.clock()
        with self.engine.begin() as conn:
            case = None
            if case_id is not None:
                case = repository.load_case(conn, case_id)
                if case is None:
                    raise CaseNotFound(case_id)
            state = case.state if case else None

            owner = EVENT_OWNER.get(event)
            if owner is not None and source is not owner:
                return self._block(conn, case, event, "system_owned_event", now, payload=payload)
            if outcome is not None:
                # Before the guards run: DeliveryConfirmed reads the finished execution row.
                self._record_outcome(conn, case, event, outcome, now)

            ctx = GuardContext(
                case=case,
                event=event,
                payload=payload,
                now=now,
                ports=self.ports,
                source=source,
                approval=repository.load_approval(conn, payload["approval_id"]) if payload.get("approval_id") else None,
                execution=repository.load_execution(conn, payload["execution_id"]) if payload.get("execution_id") else None,
                approval_already_used=repository.approval_used(conn, payload["approval_id"])
                if payload.get("approval_id")
                else False,
            )
            resolution = resolve(state, event, ctx)
            if resolution.transition is None:
                return self._block(conn, case, event, resolution.reason, now, payload=payload)
            row = resolution.transition

            if case is None:
                after = new_case(self.new_case_id(), payload["patient_id"], now)
            else:
                after = replace(apply_effects(case, row, ctx), state_version=case.state_version + 1, updated_at=now)

            candidate = self._audit_entry(case, after, event, payload, resolution, now)
            trace = repository.load_trace(conn, after.case_id) if case else []
            violation = self.monitor.check(trace, candidate)
            if violation:
                blocked = self._block(conn, case, event, f"temporal_violation:{violation}", now, payload=payload)
                return replace(blocked, temporal_violation=violation)

            if case is None:
                repository.insert_case(conn, after)
            elif repository.update_case(conn, after, expected_version=case.state_version) == 0:
                raise _StaleVersion(case.case_id)
            audit_id = repository.insert_audit(conn, candidate)
            if Effect.RECORD_EXECUTION_INTENT in row.effects:
                repository.insert_execution(conn, self._execution_intent(after, payload))
            if Effect.CONSUME_APPROVAL in row.effects:
                if repository.consume_approval(conn, ctx.approval.approval_id, now) == 0:
                    raise _StaleVersion(case.case_id)
            override_id = payload.get("policy_review_override_id")
            if event in POLICY_DECISION_EVENTS and override_id:
                # Policy design decision 4: a PolicyReview override is consumed by the next
                # Policy decision, whatever it is, in the same transaction.
                if repository.consume_approval(conn, override_id, now) == 0:
                    raise _StaleVersion(case.case_id)
            return TransitionResult(after.case_id, True, state, row.target, audit_id=audit_id)

    # --- the Tool Executor's writes (Execution design §3) --------------------------------

    def start_execution(self, case_id: str, execution_id: str) -> StartResult:
        """Everything that must be true, and recorded, before an external call - one transaction.

        Re-verifies the decision against the executions 'intent' row (ExecutorReverified),
        checks AttemptsAvailable on the count BEFORE the increment, increments attempt_count,
        consumes a ContentApproval for medical output, and writes the TOOL_EXECUTION_STARTED /
        AUDIT_RECORDED pair - both checked by the Temporal Monitor before the commit.
        """
        for _ in range(MAX_REPROCESS + 1):
            try:
                return self._start_once(case_id, execution_id)
            except _StaleVersion:
                continue
        raise ReprocessLimitExceeded(f"start of {execution_id} on {case_id}: state_version kept changing")

    def _start_once(self, case_id: str, execution_id: str) -> StartResult:
        now = self.clock()
        with self.engine.begin() as conn:
            case = repository.load_case(conn, case_id)
            if case is None:
                raise CaseNotFound(case_id)
            execution = repository.load_execution(conn, execution_id)
            approval = (repository.load_approval(conn, execution.approval_id)
                        if execution is not None and execution.approval_id else None)
            started_payload = {"execution_id": execution_id}

            reason = verify_start(case, execution, approval, now)
            if reason is None and case.attempt_count >= MAX_ATTEMPTS:
                reason = "attempts_exhausted"
            if reason is not None:
                if execution is not None:
                    repository.set_execution_status(conn, execution_id, ("intent",), "failed", now)
                self._block(conn, case, Event.TOOL_EXECUTION_STARTED, reason, now, payload=started_payload)
                return StartResult(False, reason, execution=execution)

            after = replace(case, attempt_count=case.attempt_count + 1,
                            state_version=case.state_version + 1, updated_at=now)
            evidence = {"InPlan": True, "IdentityVerified": True, "PatientContextPresent": True,
                        "AttemptsAvailable": True, "medical_content_flag": execution.medical_content_flag}
            if execution.medical_content_flag:
                evidence["ContentApprovalValid"] = True
            common = dict(case_id=case.case_id, patient_id=case.patient_id, record_type="ExecutionStarted",
                          state_before=case.state.value, state_after=case.state.value,
                          rule_version=self.rule_version, recorded_at=now, action=execution.action,
                          execution_id=execution_id, attempt_number=after.attempt_count,
                          retry_cycle=after.retry_cycle, content_hash=execution.content_hash,
                          approval_id=execution.approval_id)
            started = AuditEntry(event=Event.TOOL_EXECUTION_STARTED.value, guards=evidence, **common)
            recorded = AuditEntry(event=Event.AUDIT_RECORDED.value, **common)

            trace = repository.load_trace(conn, case_id)
            violation = self.monitor.check(trace, started) or self.monitor.check([*trace, started], recorded)
            if violation:
                repository.set_execution_status(conn, execution_id, ("intent",), "failed", now)
                reason = f"temporal_violation:{violation}"
                self._block(conn, case, Event.TOOL_EXECUTION_STARTED, reason, now, payload=started_payload)
                return StartResult(False, reason, temporal_violation=violation, execution=execution)

            if repository.update_case(conn, after, expected_version=case.state_version) == 0:
                raise _StaleVersion(case_id)
            if repository.set_execution_status(conn, execution_id, ("intent",), "started", now) == 0:
                raise _StaleVersion(case_id)
            if execution.medical_content_flag and repository.consume_approval(conn, execution.approval_id, now) == 0:
                raise _StaleVersion(case_id)
            repository.insert_audit(conn, started)
            repository.insert_audit(conn, recorded)
            return StartResult(True, execution=execution)

    def _record_outcome(self, conn: Connection, case: CaseRecord, event: Event,
                        outcome: ExecutionOutcome, now: datetime) -> None:
        """Finish the executions row and write its outcome audit row (§12.2)."""
        execution = repository.load_execution(conn, outcome.execution_id)
        if execution is None or execution.case_id != case.case_id:
            raise ExecutionStateError(f"no execution {outcome.execution_id} on {case.case_id}")
        if repository.set_execution_status(conn, outcome.execution_id, ("started",), outcome.status, now) == 0:
            raise ExecutionStateError(f"execution {outcome.execution_id} is not running")
        repository.insert_audit(conn, AuditEntry(
            case_id=case.case_id,
            patient_id=case.patient_id,
            record_type=OUTCOME_RECORD_TYPES[outcome.status],
            event=event.value,
            state_before=case.state.value,
            state_after=case.state.value,
            rule_version=self.rule_version,
            recorded_at=now,
            action=execution.action,
            execution_id=execution.execution_id,
            policy_reasons=[outcome.reason] if outcome.reason else [],
            attempt_number=execution.attempt_number,
            retry_cycle=execution.retry_cycle,
            outcome=OUTCOME_VALUES[outcome.status],
            content_hash=execution.content_hash,
        ))

    @staticmethod
    def _execution_intent(after: CaseRecord, payload: Mapping[str, Any]) -> ExecutionRecord:
        """The outbox row POLICY_ALLOWED writes: the decision and what it is bound to (§3.1)."""
        attempt_number = after.attempt_count + 1
        evidence = payload.get("evidence") or {}
        return ExecutionRecord(
            execution_id=payload["execution_id"],
            case_id=after.case_id,
            patient_id=after.patient_id,
            action=after.current_action.value,
            step=after.current_step,
            retry_cycle=after.retry_cycle,
            attempt_number=attempt_number,
            idempotency_key=f"{after.case_id}:{after.current_step}:{after.retry_cycle}:{attempt_number}",
            status="intent",
            decision_token=payload["decision_token"],
            state_version=after.state_version,
            plan_hash=after.plan_hash,
            approval_id=payload.get("content_approval_id"),
            content_hash=payload.get("content_hash"),
            medical_content_flag=evidence.get("medical_content_flag") is True,
        )

    def _block(
        self,
        conn: Connection,
        case: CaseRecord | None,
        event: Event,
        reason: str,
        now: datetime,
        *,
        check_version: bool = True,
        payload: Mapping[str, Any] | None = None,
    ) -> TransitionResult:
        if case is None:  # a rejected REQUEST_SUBMITTED: there is no case row to attach an audit row to
            return TransitionResult(None, False, None, None, reason=reason)
        if check_version and repository.confirm_version(conn, case.case_id, case.state_version) == 0:
            # F4: the row moved on since `case` was read - reprocess against the fresh State
            # instead of writing a Blocked verdict that may no longer be correct.
            raise _StaleVersion(case.case_id)
        entry = AuditEntry(
            case_id=case.case_id,
            patient_id=case.patient_id,
            record_type="Blocked",
            event=event.value,
            state_before=case.state.value,
            state_after=case.state.value,
            rule_version=self.rule_version,
            recorded_at=now,
            policy_reasons=[reason],
            attempt_number=case.attempt_count,
            retry_cycle=case.retry_cycle,
            # M1: keep approval_id/execution_id from the payload; record_blocked() has no
            # payload (the Escalation Coordinator's invalid-signal path), so both stay None.
            approval_id=payload.get("approval_id") if payload else None,
            execution_id=payload.get("execution_id") if payload else None,
        )
        audit_id = repository.insert_audit(conn, entry)
        return TransitionResult(case.case_id, False, case.state, case.state, reason=reason, audit_id=audit_id)

    def _audit_entry(
        self,
        before: CaseRecord | None,
        after: CaseRecord,
        event: Event,
        payload: Mapping[str, Any],
        resolution: Resolution,
        now: datetime,
    ) -> AuditEntry:
        proposal = payload.get("proposed_action")
        action = payload.get("action") or (proposal.get("action") if isinstance(proposal, Mapping) else None)
        if action is None and before is not None and before.current_action is not None:
            action = before.current_action.value
        return AuditEntry(
            case_id=after.case_id,
            patient_id=after.patient_id,
            record_type="Transition",
            event=event.value,
            state_before=before.state.value if before else None,
            state_after=after.state.value,
            rule_version=self.rule_version,
            recorded_at=now,
            # M1: real guard results win on a name clash - evidence can only add, never override.
            guards={**_policy_evidence(event, payload), **resolution.guard_results},
            action=action,
            execution_id=payload.get("execution_id"),
            policy_result=payload.get("policy_result"),
            policy_reasons=list(payload.get("policy_reasons", [])),
            attempt_number=after.attempt_count,
            retry_cycle=after.retry_cycle,
            approval_id=payload.get("approval_id"),
            content_hash=payload.get("content_hash"),
        )
