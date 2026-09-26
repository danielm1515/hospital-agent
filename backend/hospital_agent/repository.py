"""Row <-> record mapping for the four tables.

Every function takes an open Connection: the caller owns the transaction.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, insert, or_, select, text, true, tuple_, update
from sqlalchemy.engine import Connection, RowMapping

from .case import ApprovalRecord, CaseRecord, ExecutionRecord
from .db import approvals, audit_log, cases, executions
from .naming import EscalationKind, Event, SafetyLevel, State


@dataclass(frozen=True)
class AuditEntry:
    """One audit_log row (§12.2). Append-only: there is no update function."""

    case_id: str
    patient_id: str
    record_type: str  # Transition | ExecutionStarted | ... | Blocked
    event: str
    state_before: str | None  # None = Initial
    state_after: str | None
    rule_version: str
    recorded_at: datetime
    guards: dict[str, bool] = field(default_factory=dict)
    action: str | None = None
    execution_id: str | None = None
    policy_result: str | None = None
    policy_reasons: list[str] = field(default_factory=list)
    attempt_number: int | None = None
    retry_cycle: int | None = None
    outcome: str | None = None
    approval_id: str | None = None
    content_hash: str | None = None
    audit_id: int | None = None


# --- cases -------------------------------------------------------------------------


def _case_from_row(row: RowMapping) -> CaseRecord:
    return CaseRecord(
        case_id=row["case_id"],
        patient_id=row["patient_id"],
        state=State(row["state"]),
        state_version=row["state_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        intent=row["intent"],
        safety_level=SafetyLevel(row["safety_level"]) if row["safety_level"] else None,
        identity_verified=row["identity_verified"],
        plan_hash=row["plan_hash"],
        ordered_steps=row["ordered_steps"],
        current_step=row["current_step"],
        retry_cycle=row["retry_cycle"],
        attempt_count=row["attempt_count"],
        required_documents=row["required_documents"],
        held_documents=list(row["held_documents"]),
        escalation_kind=EscalationKind(row["escalation_kind"]) if row["escalation_kind"] else None,
        escalated_from_state=State(row["escalated_from_state"]) if row["escalated_from_state"] else None,
        patient_deadline=row["patient_deadline"],
        appointment_at=row["appointment_at"],
        human_engaged=row["human_engaged"],
        reply_kind=row["reply_kind"],
        requested_document=row["requested_document"],
        appointment_id=row["appointment_id"],
        answered_appointment_id=row["answered_appointment_id"],
        department=row["department"],
        exam_type_label=row["exam_type_label"],
        instruction_source_id=row["instruction_source_id"],
        instruction_version=row["instruction_version"],
        upcoming_count=row["upcoming_count"],
    )


def _case_values(case: CaseRecord) -> dict[str, Any]:
    """Every column except case_id and created_at, as plain values."""
    return {
        "patient_id": case.patient_id,
        "state": case.state.value,
        "state_version": case.state_version,
        "intent": case.intent,
        "safety_level": case.safety_level.value if case.safety_level else None,
        "identity_verified": case.identity_verified,
        "plan_hash": case.plan_hash,
        "ordered_steps": case.ordered_steps,
        "current_step": case.current_step,
        "retry_cycle": case.retry_cycle,
        "attempt_count": case.attempt_count,
        "required_documents": case.required_documents,
        "held_documents": case.held_documents,
        "escalation_kind": case.escalation_kind.value if case.escalation_kind else None,
        "escalated_from_state": case.escalated_from_state.value if case.escalated_from_state else None,
        "patient_deadline": case.patient_deadline,
        "appointment_at": case.appointment_at,
        "human_engaged": case.human_engaged,
        "reply_kind": case.reply_kind,
        "requested_document": case.requested_document,
        "appointment_id": case.appointment_id,
        "answered_appointment_id": case.answered_appointment_id,
        "department": case.department,
        "exam_type_label": case.exam_type_label,
        "instruction_source_id": case.instruction_source_id,
        "instruction_version": case.instruction_version,
        "upcoming_count": case.upcoming_count,
        "updated_at": case.updated_at,
    }


def load_case(conn: Connection, case_id: str) -> CaseRecord | None:
    row = conn.execute(select(cases).where(cases.c.case_id == case_id)).mappings().first()
    return None if row is None else _case_from_row(row)


def list_cases(conn: Connection, state: State | None = None) -> list[CaseRecord]:
    query = select(cases).order_by(cases.c.updated_at.desc(), cases.c.case_id)
    if state is not None:
        query = query.where(cases.c.state == state.value)
    return [_case_from_row(row) for row in conn.execute(query).mappings()]


@dataclass(frozen=True)
class CaseListRow:
    """One `GET /api/staff/cases` item's columns (staff-fixes design Task 3): an explicit
    column list that skips the JSONB `ordered_steps` / `held_documents` / `required_documents`
    the list screen never shows."""

    case_id: str
    patient_id: str
    state: State
    intent: str | None
    safety_level: SafetyLevel | None
    escalation_kind: EscalationKind | None
    escalated_from_state: State | None
    created_at: datetime
    updated_at: datetime


_CASE_LIST_COLUMNS = (
    cases.c.case_id,
    cases.c.patient_id,
    cases.c.state,
    cases.c.intent,
    cases.c.safety_level,
    cases.c.escalation_kind,
    cases.c.escalated_from_state,
    cases.c.created_at,
    cases.c.updated_at,
)


def _case_list_row(row: RowMapping) -> CaseListRow:
    return CaseListRow(
        case_id=row["case_id"],
        patient_id=row["patient_id"],
        state=State(row["state"]),
        intent=row["intent"],
        safety_level=SafetyLevel(row["safety_level"]) if row["safety_level"] else None,
        escalation_kind=EscalationKind(row["escalation_kind"]) if row["escalation_kind"] else None,
        escalated_from_state=State(row["escalated_from_state"]) if row["escalated_from_state"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# Staff-fixes design Task 3/5 fix round 1 (M5): each keyset cursor carries a one-letter
# prefix naming which list produced it, so a cursor from `GET /api/staff/cases` (or a
# hand-crafted one) is refused - not silently misread - by `GET /api/staff/reviews`, and
# the other way around.
_CASES_CURSOR_KIND = "c"
_QUEUE_CURSOR_KIND = "r"


def _encode_cursor(kind: str, at: datetime, case_id: str) -> str:
    raw = f"{at.isoformat()}|{case_id}"
    payload = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
    return f"{kind}|{payload}"


def _decode_cursor(kind: str, cursor: str) -> tuple[datetime, str]:
    """Raises ValueError on anything that is not a cursor this list produced (API 422
    `invalid_cursor`): a malformed cursor, one built for the other list (wrong kind
    prefix), or a bare `case_id` with no timezone offset (M5: a naive datetime is refused
    rather than silently compared to the aware `entered_at`/`updated_at` columns)."""
    prefix, sep, payload = cursor.partition("|")
    if not sep or prefix != kind:
        raise ValueError("invalid_cursor")
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
        at_text, case_id = raw.split("|", 1)
        at = datetime.fromisoformat(at_text)
    except (binascii.Error, ValueError, UnicodeDecodeError) as error:
        raise ValueError("invalid_cursor") from error
    if at.tzinfo is None or not case_id:
        raise ValueError("invalid_cursor")
    return at, case_id


def encode_cases_cursor(updated_at: datetime, case_id: str) -> str:
    """The opaque `next_cursor` of `GET /api/staff/cases` (staff-fixes design Task 3): a
    `c|` kind prefix over base64 of `updated_at|case_id` (design Task 5 fix round 1, M5)."""
    return _encode_cursor(_CASES_CURSOR_KIND, updated_at, case_id)


def decode_cases_cursor(cursor: str) -> tuple[datetime, str]:
    return _decode_cursor(_CASES_CURSOR_KIND, cursor)


def encode_queue_cursor(entered_at: datetime, case_id: str) -> str:
    """The opaque `next_cursor` of `GET /api/staff/reviews` (staff-fixes design Task 5): an
    `r|` kind prefix, so it can never be mistaken for a case-list cursor (M5)."""
    return _encode_cursor(_QUEUE_CURSOR_KIND, entered_at, case_id)


def decode_queue_cursor(cursor: str) -> tuple[datetime, str]:
    return _decode_cursor(_QUEUE_CURSOR_KIND, cursor)


def list_cases_page(
    conn: Connection,
    states: list[State] | None,
    limit: int,
    cursor: tuple[datetime, str] | None,
    escalation_kind: EscalationKind | None = None,
) -> tuple[list[CaseListRow], bool]:
    """Keyset-paginated (staff-fixes design Task 3): one `SELECT` with an explicit column
    list, ordered `updated_at DESC, case_id DESC`. Fetches `limit + 1` rows so the caller
    knows whether there is a next page without a second query. `escalation_kind` narrows a
    `group=staff` filter further (staff-fixes design Task 4)."""
    query = select(*_CASE_LIST_COLUMNS)
    if states is not None:
        query = query.where(cases.c.state.in_([s.value for s in states]))
    if escalation_kind is not None:
        query = query.where(cases.c.escalation_kind == escalation_kind.value)
    if cursor is not None:
        at, case_id = cursor
        query = query.where(tuple_(cases.c.updated_at, cases.c.case_id) < tuple_(at, case_id))
    query = query.order_by(cases.c.updated_at.desc(), cases.c.case_id.desc()).limit(limit + 1)
    rows = [_case_list_row(row) for row in conn.execute(query).mappings()]
    has_more = len(rows) > limit
    return rows[:limit], has_more


def list_patient_cases(conn: Connection, patient_id: str) -> list[CaseRecord]:
    """One patient's cases, newest first."""
    query = (select(cases).where(cases.c.patient_id == patient_id)
             .order_by(cases.c.created_at.desc(), cases.c.case_id.desc()))
    return [_case_from_row(row) for row in conn.execute(query).mappings()]


def insert_case(conn: Connection, case: CaseRecord) -> None:
    conn.execute(insert(cases).values(case_id=case.case_id, created_at=case.created_at, **_case_values(case)))


def update_case(conn: Connection, case: CaseRecord, expected_version: int) -> int:
    """Optimistic lock (§18.2): returns the number of rows updated - 0 means stale."""
    result = conn.execute(
        update(cases)
        .where(cases.c.case_id == case.case_id, cases.c.state_version == expected_version)
        .values(**_case_values(case))
    )
    return result.rowcount


# --- audit_log ---------------------------------------------------------------------


def insert_audit(conn: Connection, entry: AuditEntry) -> int:
    values = asdict(entry)
    values.pop("audit_id")
    return conn.execute(insert(audit_log).values(**values).returning(audit_log.c.audit_id)).scalar_one()


def load_trace(conn: Connection, case_id: str) -> list[AuditEntry]:
    """The case's audit rows in order - the trace the Temporal Monitor evaluates."""
    rows = conn.execute(
        select(audit_log).where(audit_log.c.case_id == case_id).order_by(audit_log.c.audit_id)
    ).mappings()
    return [AuditEntry(**dict(row)) for row in rows]


@dataclass(frozen=True)
class ReviewQueueRow:
    """One `GET /api/staff/reviews` item's columns (staff-fixes design Task 5): the case's
    own columns plus its latest entry into `AwaitingHumanReview`, from one LATERAL join."""

    case_id: str
    patient_id: str
    escalation_kind: EscalationKind | None
    escalated_from_state: State | None
    human_engaged: bool
    entered_at: datetime
    reasons: list[str]
    returned_by: str | None


def _queue_row(row: RowMapping) -> ReviewQueueRow:
    event, state_before = row["event"], row["state_before"]
    if event == Event.PATIENT_REPLY_SUBMITTED.value:
        returned_by = "patient_reply"
    elif event == Event.TIMEOUT_EXPIRED.value and state_before == State.AWAITING_PATIENT_REPLY.value:
        returned_by = "reply_timeout"
    else:
        returned_by = None
    return ReviewQueueRow(
        case_id=row["case_id"],
        patient_id=row["patient_id"],
        escalation_kind=EscalationKind(row["escalation_kind"]) if row["escalation_kind"] else None,
        escalated_from_state=State(row["escalated_from_state"]) if row["escalated_from_state"] else None,
        human_engaged=row["human_engaged"],
        entered_at=row["entered_at"],
        reasons=list(row["reasons"]),
        returned_by=returned_by,
    )


def queue_page(
    conn: Connection, limit: int, cursor: tuple[datetime, str] | None
) -> tuple[list[ReviewQueueRow], bool]:
    """The cases waiting for a reviewer, newest entry into `AwaitingHumanReview` first
    (staff-fixes design Task 5): one statement, with a LATERAL join to each case's latest
    `record_type='Transition' AND state_after='AwaitingHumanReview'` row (a `Blocked` row can
    carry that `state_after` too, hence the `record_type` filter - the FSM has no Transition
    from AwaitingHumanReview back into itself). `reasons` and `returned_by` come from that
    same row, replacing the Python-side `_escalation_reasons` / `_returned_by` scan of the
    whole trace. Ordered `entered_at DESC, case_id ASC`, keyset-paginated the same way as
    Task 3's case list, fetching `limit + 1` rows to know whether there is a next page.

    Fix round 1 (M4): the join is a LEFT (outer) LATERAL, and `entered_at`/`reasons` are
    read through `COALESCE(..., cases.updated_at)` / `COALESCE(..., '[]')` - every place
    the raw `entry` columns would otherwise appear, including the keyset predicate and the
    ORDER BY. A case in AwaitingHumanReview always has its entry row in practice (only
    EscalationCoordinator.signal() writes that State, in the same transaction as the row),
    but an INNER join would silently drop the case from the queue if that ever stopped
    holding - a queue that loses cases is worse than one that shows a fallback time.
    """
    entry = (
        select(
            audit_log.c.recorded_at.label("entered_at"),
            audit_log.c.policy_reasons.label("reasons"),
            audit_log.c.event.label("event"),
            audit_log.c.state_before.label("state_before"),
        )
        .where(
            audit_log.c.case_id == cases.c.case_id,
            audit_log.c.record_type == "Transition",
            audit_log.c.state_after == State.AWAITING_HUMAN_REVIEW.value,
        )
        .order_by(audit_log.c.audit_id.desc())
        .limit(1)
        .lateral("entry")
    )
    entered_at_expr = func.coalesce(entry.c.entered_at, cases.c.updated_at)
    reasons_expr = func.coalesce(entry.c.reasons, text("'[]'::jsonb"))
    query = (
        select(
            cases.c.case_id,
            cases.c.patient_id,
            cases.c.escalation_kind,
            cases.c.escalated_from_state,
            cases.c.human_engaged,
            entered_at_expr.label("entered_at"),
            reasons_expr.label("reasons"),
            entry.c.event,
            entry.c.state_before,
        )
        .select_from(cases.join(entry, true(), isouter=True))
        .where(cases.c.state == State.AWAITING_HUMAN_REVIEW.value)
    )
    if cursor is not None:
        at, case_id = cursor
        # entered_at DESC, case_id ASC: "after the cursor" is an earlier entered_at, or the
        # same entered_at with a greater case_id (the tuple_() shortcut needs both columns
        # sorted the same direction, which is not the case here). Repeats entered_at_expr,
        # not the "entered_at" select-list label: a WHERE clause cannot reference that.
        query = query.where(or_(entered_at_expr < at, and_(entered_at_expr == at, cases.c.case_id > case_id)))
    query = query.order_by(entered_at_expr.desc(), cases.c.case_id.asc()).limit(limit + 1)
    rows = [_queue_row(row) for row in conn.execute(query).mappings()]
    has_more = len(rows) > limit
    return rows[:limit], has_more


# --- approvals ---------------------------------------------------------------------


def load_approval(conn: Connection, approval_id: str) -> ApprovalRecord | None:
    row = conn.execute(select(approvals).where(approvals.c.approval_id == approval_id)).mappings().first()
    return None if row is None else ApprovalRecord(**dict(row))


def insert_approval(conn: Connection, approval: ApprovalRecord) -> None:
    conn.execute(insert(approvals).values(**asdict(approval)))


def confirm_version(conn: Connection, case_id: str, expected_version: int) -> int:
    """A no-op conditional UPDATE that also locks the row (F4): 0 means the case moved on.

    Used before writing a Blocked row for an existing case, so a verdict based on a
    stale in-memory snapshot is not committed to audit_log. Deliberately an UPDATE, not
    SELECT ... FOR UPDATE, so it cannot deadlock against a concurrent transaction doing
    the same (see the D15 barrier test).
    """
    result = conn.execute(
        update(cases)
        .where(cases.c.case_id == case_id, cases.c.state_version == expected_version)
        .values(state_version=cases.c.state_version)
    )
    return result.rowcount


def approval_used(conn: Connection, approval_id: str) -> bool:
    """True if approval_id already appears on a committed Transition audit row (F1: replay guard)."""
    row = conn.execute(
        select(audit_log.c.audit_id)
        .where(audit_log.c.approval_id == approval_id, audit_log.c.record_type == "Transition")
        .limit(1)
    ).first()
    return row is not None


def open_policy_review_override(
    conn: Connection, case_id: str, plan_hash: str | None, current_step: int | None
) -> ApprovalRecord | None:
    """The approval the case's latest committed HUMAN_APPROVED used, if it still is a valid
    open PolicyReview override for this plan step (§3.1 PolicyReviewOverrideValid).

    F1: this must NOT be "any unconsumed PolicyReview approval for the step" - a double-submit
    can leave two such approvals open, and only the one that actually resumed the case (the
    approval_id on its latest committed HUMAN_APPROVED audit row) is a valid override. OPA
    judges its validity; this only finds the candidate the Policy Service presents.
    """
    latest = conn.execute(
        select(audit_log.c.approval_id)
        .where(
            audit_log.c.case_id == case_id,
            audit_log.c.record_type == "Transition",
            audit_log.c.event == "HUMAN_APPROVED",
        )
        .order_by(audit_log.c.audit_id.desc())
        .limit(1)
    ).first()
    if latest is None or latest.approval_id is None:
        return None
    approval = load_approval(conn, latest.approval_id)
    if (
        approval is None
        or approval.approval_type != "WorkflowDecision"
        or approval.escalation_kind != "PolicyReview"
        or approval.decision != "approve"
        or approval.consumed_at is not None
        or approval.plan_hash != plan_hash
        or approval.current_step != current_step
    ):
        return None
    return approval


def content_approvals_for(conn: Connection, case_id: str, action: str | None) -> list[ApprovalRecord]:
    """This case's approvals, newest last; `action` narrows to one Action Registry entry."""
    query = select(approvals).where(approvals.c.case_id == case_id)
    if action is not None:
        query = query.where(approvals.c.action == action)
    rows = conn.execute(query.order_by(approvals.c.granted_at, approvals.c.approval_id)).mappings()
    return [ApprovalRecord(**dict(row)) for row in rows]


def consume_approval(conn: Connection, approval_id: str, now: datetime) -> int:
    """Single use (§18.2): returns 0 if the approval was already consumed."""
    result = conn.execute(
        update(approvals)
        .where(approvals.c.approval_id == approval_id, approvals.c.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    return result.rowcount


# --- executions --------------------------------------------------------------------


def load_execution(conn: Connection, execution_id: str) -> ExecutionRecord | None:
    row = conn.execute(select(executions).where(executions.c.execution_id == execution_id)).mappings().first()
    return None if row is None else ExecutionRecord(**dict(row))


def insert_execution(conn: Connection, execution: ExecutionRecord) -> None:
    conn.execute(insert(executions).values(**asdict(execution)))


def set_execution_status(
    conn: Connection, execution_id: str, from_statuses: tuple[str, ...], to_status: str, now: datetime
) -> int:
    """Move an execution along intent -> started -> succeeded|failed|unknown (§18.2).

    Returns 0 if the row is not in one of `from_statuses` (it moved on, or never existed).
    started_at is stamped on entering 'started'; finished_at on any final status.
    """
    stamp = {"started_at": now} if to_status == "started" else {"finished_at": now}
    result = conn.execute(
        update(executions)
        .where(executions.c.execution_id == execution_id, executions.c.status.in_(from_statuses))
        .values(status=to_status, **stamp)
    )
    return result.rowcount


def executions_of_case(conn: Connection, case_id: str) -> list[ExecutionRecord]:
    """This case's executions, oldest first."""
    rows = conn.execute(
        select(executions).where(executions.c.case_id == case_id).order_by(executions.c.execution_id)
    ).mappings()
    return [ExecutionRecord(**dict(row)) for row in rows]


def executions_with_status(conn: Connection, status: str) -> list[ExecutionRecord]:
    rows = conn.execute(
        select(executions).where(executions.c.status == status).order_by(executions.c.started_at)
    ).mappings()
    return [ExecutionRecord(**dict(row)) for row in rows]


def expired_patient_deadlines(conn: Connection, now: datetime) -> list[CaseRecord]:
    """Cases waiting for the patient - a document, or a reply to a staff request (sub-project 15) -
    whose deadline has passed: the SLA Worker's scan (§18.2 index)."""
    rows = conn.execute(
        select(cases)
        .where(cases.c.state.in_((State.AWAITING_PATIENT_INPUT.value, State.AWAITING_PATIENT_REPLY.value)),
               cases.c.patient_deadline <= now)
        .order_by(cases.c.patient_deadline)
    ).mappings()
    return [_case_from_row(row) for row in rows]
