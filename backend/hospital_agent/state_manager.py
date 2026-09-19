"""State Manager - the only writer of State (spec §3, §12, §18.2; design §7).

apply() runs one transaction per event:

    read cases -> ownership check -> resolve the §3 row -> Temporal Monitor
    -> UPDATE cases WHERE state_version + INSERT audit_log (+ consume approval) -> COMMIT

A blocked event writes one Blocked audit row and changes nothing else. A stale
state_version rolls the whole transaction back and the event is re-processed
against the fresh row, at most MAX_REPROCESS times (design §12.2), then fails closed.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.engine import Connection, Engine

from . import repository
from .case import CaseRecord, new_case
from .escalation import EscalationCoordinator
from .fsm import Effect, Resolution, apply_effects, resolve
from .guards import GuardContext, GuardPorts
from .naming import EVENT_OWNER, NON_TRANSITION_EVENTS, Component, EscalationKind, Event, State, canonical_event
from .repository import AuditEntry

MAX_REPROCESS = 3
# §12.2 rule_version. The Policy / LLM versions join it in later sub-projects.
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
    ) -> None:
        self.engine = engine
        self.monitor = monitor
        self.ports = ports
        self.clock = clock
        self.new_case_id = new_case_id
        self.escalation = EscalationCoordinator(self)

    # --- public API ----------------------------------------------------------------

    def apply(
        self,
        case_id: str | None,
        event: Event | str,
        payload: Mapping[str, Any] | None = None,
        source: Component = Component.EXTERNAL,
    ) -> TransitionResult:
        """Apply one event. case_id is None only for REQUEST_SUBMITTED."""
        event = canonical_event(event)
        if event in NON_TRANSITION_EVENTS:
            raise NonTransitionEvent(f"{event} is recorded by the Tool Executor, not applied as a transition")
        payload = dict(payload or {})
        for _ in range(MAX_REPROCESS + 1):
            try:
                result = self._apply_once(case_id, event, payload, source)
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

    def _apply_once(self, case_id: str | None, event: Event, payload: dict[str, Any], source: Component) -> TransitionResult:
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
            if Effect.CONSUME_APPROVAL in row.effects:
                if repository.consume_approval(conn, ctx.approval.approval_id, now) == 0:
                    raise _StaleVersion(case.case_id)
            return TransitionResult(after.case_id, True, state, row.target, audit_id=audit_id)

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
            rule_version=RULE_VERSION,
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

    @staticmethod
    def _audit_entry(
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
            rule_version=RULE_VERSION,
            recorded_at=now,
            guards=resolution.guard_results,
            action=action,
            execution_id=payload.get("execution_id"),
            policy_result=payload.get("policy_result"),
            policy_reasons=list(payload.get("policy_reasons", [])),
            attempt_number=after.attempt_count,
            retry_cycle=after.retry_cycle,
            approval_id=payload.get("approval_id"),
            content_hash=payload.get("content_hash"),
        )
