"""The admin metrics screen's numbers (sub-project 14, design
docs/superpowers/specs/2026-09-24-admin-metrics-design.md).

Read-only. Every number aggregates tables the agent already writes - audit_log, executions,
cases and approvals - so a metric can never disagree with the Audit (design §3). compute()
runs every group in one REPEATABLE READ, READ ONLY transaction: all groups see the same
snapshot, and a slow query ends the whole answer (MetricsUnavailable), never a partial one
(design §5).

Two kinds of window (design §4.0): the cohort group (flow) counts the cases *opened* in
[start, end); the event groups count what *happened* in it. Durations are seconds; a
percentile over no rows is None, never 0.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from psycopg import errors as pg_errors
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError

MAX_WINDOW = timedelta(days=90)
STATEMENT_TIMEOUT = "5s"


class InvalidWindow(ValueError):
    """`code` is the API's 422 detail: invalid_range or range_too_large."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MetricsUnavailable(Exception):
    """A query ran past STATEMENT_TIMEOUT: the whole answer is refused, never a partial one."""


@dataclass(frozen=True)
class Window:
    start: datetime  # inclusive
    end: datetime  # exclusive

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise InvalidWindow("invalid_range")
        if self.end - self.start > MAX_WINDOW:
            raise InvalidWindow("range_too_large")

    @property
    def params(self) -> dict[str, datetime]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True)
class Durations:
    """Seconds. `count` is the number of measured intervals; the rest are None when it is 0."""

    count: int
    p50: float | None
    p95: float | None
    max: float | None


EMPTY_DURATIONS = Durations(0, None, None, None)

# The event groups' window, on an unqualified recorded_at (a query over audit_log alone).
_EVENTS = "recorded_at >= :start AND recorded_at < :end"


def _durations_sql(interval: str) -> str:
    # extract() is numeric and percentile_cont double precision: cast, so every value is a float.
    seconds = f"extract(epoch FROM {interval})::double precision"
    return (f"count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY {seconds}), "
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY {seconds}), max({seconds})")


def _durations(row: Sequence[Any]) -> Durations:
    return Durations(int(row[0]), row[1], row[2], row[3])


def _counts(conn: Connection, sql: str, params: Mapping[str, Any]) -> dict[str, int]:
    return {str(key): int(count) for key, count in conn.execute(text(sql), params)}


# --- A: flow (cohort - the cases opened in the window; design §4.1) ------------------------

COMPLETION_EVENTS = ("CASE_RESOLVED", "HUMAN_RESOLVED_CASE")
_COHORT = "c.created_at >= :start AND c.created_at < :end"

# design §4.1 A3: one outcome per case, first match wins. Not cases.intent alone -
# RECORD_CLASSIFICATION runs only on INTENT_CLASSIFIED, so a medical question or an
# escalation at classification leaves it NULL.
_OUTCOME = f"""
    SELECT CASE
             WHEN EXISTS (SELECT 1 FROM audit_log a WHERE a.case_id = c.case_id
                            AND a.record_type = 'Transition' AND a.event = 'MEDICAL_QUESTION_DETECTED')
               THEN 'MedicalQuestion'
             WHEN EXISTS (SELECT 1 FROM audit_log a WHERE a.case_id = c.case_id
                            AND a.record_type = 'Transition' AND a.event = 'HUMAN_REVIEW_REQUIRED'
                            AND a.state_before = 'Classifying')
               THEN 'EscalatedAtClassification'
             WHEN c.intent IS NOT NULL THEN c.intent
             ELSE 'NotClassified'
           END, count(*)
    FROM cases c WHERE {_COHORT} GROUP BY 1"""


@dataclass(frozen=True)
class Flow:
    opened: int
    by_state: dict[str, int]
    by_outcome: dict[str, int]
    completion: dict[str, Durations]  # CASE_RESOLVED (automatic) / HUMAN_RESOLVED_CASE (by staff)


def flow(conn: Connection, window: Window) -> Flow:
    by_state = _counts(conn, f"SELECT c.state, count(*) FROM cases c WHERE {_COHORT} GROUP BY 1", window.params)
    completion = dict.fromkeys(COMPLETION_EVENTS, EMPTY_DURATIONS)
    rows = conn.execute(text(f"""
        SELECT a.event, {_durations_sql('a.recorded_at - c.created_at')}
        FROM cases c
        JOIN audit_log a ON a.case_id = c.case_id
                        AND a.record_type = 'Transition' AND a.state_after = 'Completed'
        WHERE {_COHORT} GROUP BY a.event"""), window.params)
    completion.update({row[0]: _durations(row[1:]) for row in rows})
    return Flow(opened=sum(by_state.values()), by_state=by_state,
                by_outcome=_counts(conn, _OUTCOME, window.params), completion=completion)


# --- B: human load (events in the window; design §4.2) -------------------------------------

DECISION_EVENTS = ("HUMAN_APPROVED", "HUMAN_RESOLVED_CASE", "HUMAN_REJECTED")
_DECISIONS = "('HUMAN_APPROVED', 'HUMAN_RESOLVED_CASE', 'HUMAN_REJECTED')"


@dataclass(frozen=True)
class HumanLoad:
    escalations_entered: int
    decisions: dict[str, int]
    decided_by_kind: dict[str, int]  # decided in the window
    open_by_kind: dict[str, int]  # open now, whatever the window
    time_to_decision: Durations
    open_now: int
    oldest_open_seconds: float | None


def human_load(conn: Connection, window: Window) -> HumanLoad:
    params = window.params
    entered = conn.execute(text(f"""
        SELECT count(*) FROM audit_log
        WHERE record_type = 'Transition' AND state_after = 'AwaitingHumanReview' AND {_EVENTS}"""),
        params).scalar_one()
    decisions = dict.fromkeys(DECISION_EVENTS, 0)
    decisions.update(_counts(conn, f"""
        SELECT event, count(*) FROM audit_log
        WHERE record_type = 'Transition' AND event IN {_DECISIONS} AND {_EVENTS} GROUP BY 1""", params))
    # design decision 6: the approval the committed transition actually used. Never a count of
    # approvals rows - _grant writes one in its own transaction, and a blocked decision (or a
    # double submit) leaves one behind.
    decided_by_kind = _counts(conn, f"""
        SELECT ap.escalation_kind, count(*) FROM audit_log a
        JOIN approvals ap ON ap.approval_id = a.approval_id
        WHERE a.record_type = 'Transition' AND a.event IN {_DECISIONS}
          AND a.recorded_at >= :start AND a.recorded_at < :end AND ap.escalation_kind IS NOT NULL
        GROUP BY 1""", params)
    # Each entry into the review queue, paired with the next transition out of it for the same
    # case - while a case waits, a human decision is the only way out (§3).
    time_to_decision = _durations(conn.execute(text(f"""
        WITH entries AS (
            SELECT case_id, audit_id, recorded_at FROM audit_log
            WHERE record_type = 'Transition' AND state_after = 'AwaitingHumanReview' AND {_EVENTS}),
        decided AS (
            SELECT e.recorded_at AS entered_at,
                   (SELECT x.recorded_at FROM audit_log x
                    WHERE x.case_id = e.case_id AND x.audit_id > e.audit_id
                      AND x.record_type = 'Transition' AND x.state_before = 'AwaitingHumanReview'
                    ORDER BY x.audit_id LIMIT 1) AS decided_at
            FROM entries e)
        SELECT {_durations_sql('decided_at - entered_at')} FROM decided WHERE decided_at IS NOT NULL"""),
        params).one())
    open_by_kind = _counts(conn, """
        SELECT escalation_kind, count(*) FROM cases
        WHERE state = 'AwaitingHumanReview' AND escalation_kind IS NOT NULL GROUP BY 1""", {})
    # now() is the transaction's start, so the age agrees with every other number in the answer.
    open_now, oldest = conn.execute(text("""
        SELECT count(*), extract(epoch FROM now() - min(entered_at))::double precision FROM (
            SELECT c.case_id, max(a.recorded_at) AS entered_at FROM cases c
            JOIN audit_log a ON a.case_id = c.case_id AND a.record_type = 'Transition'
                            AND a.state_after = 'AwaitingHumanReview'
            WHERE c.state = 'AwaitingHumanReview' GROUP BY c.case_id) open_cases""")).one()
    return HumanLoad(int(entered), decisions, decided_by_kind, open_by_kind, time_to_decision,
                     int(open_now), oldest)


# --- C: external tools (events in the window, by executions.started_at; design §4.3) ------

FAILURE_EVENTS = ("TOOL_TRANSIENT_FAILURE", "RETRY_EXHAUSTED")
FINISHED = ("succeeded", "failed", "unknown")
_STARTED = "started_at >= :start AND started_at < :end"


@dataclass(frozen=True)
class ToolAction:
    action: str
    by_status: dict[str, int]
    success_rate: float | None  # succeeded / finished; None when nothing finished
    latency: Durations  # finished_at - started_at, the whole Tool Executor call


@dataclass(frozen=True)
class FailureReason:
    outcome: str  # the audit record_type: ExecutionFailed | ExecutionUnknown
    reason: str | None  # as recorded, e.g. tool:transient_failure:timeout
    count: int


@dataclass(frozen=True)
class Tools:
    actions: list[ToolAction]
    failure_events: dict[str, int]
    failure_reasons: list[FailureReason]
    retried_calls: int
    sources: dict[str, str | None] = field(default_factory=dict)  # compute() sets it from app.state


def tools(conn: Connection, window: Window) -> Tools:
    params = window.params
    by_action: dict[str, dict[str, int]] = {}
    for action, status, count in conn.execute(text(
            f"SELECT action, status, count(*) FROM executions WHERE {_STARTED} GROUP BY 1, 2 ORDER BY 1, 2"),
            params):
        by_action.setdefault(action, {})[status] = int(count)
    latency = {row[0]: _durations(row[1:]) for row in conn.execute(text(f"""
        SELECT action, {_durations_sql('finished_at - started_at')} FROM executions
        WHERE {_STARTED} AND finished_at IS NOT NULL GROUP BY 1"""), params)}
    actions = []
    for action in sorted(by_action):
        statuses = by_action[action]
        finished = sum(statuses.get(status, 0) for status in FINISHED)
        actions.append(ToolAction(action, statuses, statuses.get("succeeded", 0) / finished if finished else None,
                                  latency.get(action, EMPTY_DURATIONS)))
    failure_events = dict.fromkeys(FAILURE_EVENTS, 0)
    failure_events.update(_counts(conn, f"""
        SELECT event, count(*) FROM audit_log
        WHERE record_type = 'Transition' AND event IN ('TOOL_TRANSIENT_FAILURE', 'RETRY_EXHAUSTED')
          AND {_EVENTS} GROUP BY 1""", params))
    failure_reasons = [FailureReason(outcome, reason, int(count)) for outcome, reason, count in conn.execute(text("""
        SELECT a.record_type, r.reason, count(*) FROM audit_log a
        LEFT JOIN LATERAL jsonb_array_elements_text(a.policy_reasons) AS r(reason) ON true
        WHERE a.record_type IN ('ExecutionFailed', 'ExecutionUnknown')
          AND a.recorded_at >= :start AND a.recorded_at < :end
        GROUP BY 1, 2 ORDER BY 1, 2"""), params)]
    retried = conn.execute(text(f"SELECT count(*) FROM executions WHERE {_STARTED} AND attempt_number > 1"),
                           params).scalar_one()
    return Tools(actions, failure_events, failure_reasons, int(retried))


# --- D: the patient's SLA (events: the waits that began in the window; design §4.4) ------


@dataclass(frozen=True)
class PatientSla:
    requests: int
    met: int  # the wait ended in DOCUMENT_UPLOADED -> Classifying
    breached: int  # the wait ended in TIMEOUT_EXPIRED
    other: int  # the wait ended in any other way out (a TemporalViolation escalation)
    waiting: int  # not ended yet
    rate: float | None  # met / (met + breached); None when neither happened


def patient_sla(conn: Connection, window: Window) -> PatientSla:
    # A wait begins with any Transition row that enters AwaitingPatientInput from somewhere
    # else - not only MISSING_INFORMATION_DETECTED from AssessingReadiness, but also
    # HUMAN_APPROVED on a Z3Counterexample / PatientSlaExpired escalation, which re-opens the
    # wait with a new patient_deadline (fsm.py, AWAITING_HUMAN_REVIEW -> AWAITING_PATIENT_INPUT).
    # state_before must differ too: a rejected upload is a DOCUMENT_UPLOADED self-loop and is
    # not a new wait. Each wait is ended by the next transition that leaves
    # AwaitingPatientInput; state_after must differ there for the same self-loop reason. The
    # metric reports the system's own verdict - which event ended the wait - and never
    # re-judges it against cases.patient_deadline.
    exits: dict[str | None, int] = {exit_event: int(count) for exit_event, count in conn.execute(text(f"""
        WITH entries AS (
            SELECT case_id, audit_id FROM audit_log
            WHERE record_type = 'Transition' AND state_after = 'AwaitingPatientInput'
              AND state_before <> 'AwaitingPatientInput' AND {_EVENTS})
        SELECT (SELECT x.event FROM audit_log x
                WHERE x.case_id = e.case_id AND x.audit_id > e.audit_id AND x.record_type = 'Transition'
                  AND x.state_before = 'AwaitingPatientInput' AND x.state_after <> 'AwaitingPatientInput'
                ORDER BY x.audit_id LIMIT 1) AS exit_event,
               count(*)
        FROM entries e GROUP BY 1"""), window.params)}
    requests = sum(exits.values())
    met, breached, waiting = exits.get("DOCUMENT_UPLOADED", 0), exits.get("TIMEOUT_EXPIRED", 0), exits.get(None, 0)
    return PatientSla(requests, met, breached, requests - met - breached - waiting, waiting,
                      met / (met + breached) if met + breached else None)


# --- E: the policy layers (events in the window; design §4.5) ----------------------------

POLICY_EVENTS = ("POLICY_ALLOWED", "POLICY_DENIED", "POLICY_HUMAN_REVIEW_REQUIRED")


@dataclass(frozen=True)
class Policy:
    decisions: dict[str, int]
    blocked: int
    blocked_by_reason: dict[str, int]
    blocked_by_event: dict[str, int]


def policy(conn: Connection, window: Window) -> Policy:
    params = window.params
    decisions = dict.fromkeys(POLICY_EVENTS, 0)
    decisions.update(_counts(conn, f"""
        SELECT event, count(*) FROM audit_log
        WHERE record_type = 'Transition'
          AND event IN ('POLICY_ALLOWED', 'POLICY_DENIED', 'POLICY_HUMAN_REVIEW_REQUIRED')
          AND {_EVENTS} GROUP BY 1""", params))
    blocked_by_event = _counts(conn, f"""
        SELECT event, count(*) FROM audit_log WHERE record_type = 'Blocked' AND {_EVENTS} GROUP BY 1""", params)
    # design §4.5 E2: a Blocked row is written with guards = {}; its reason is in policy_reasons.
    blocked_by_reason = _counts(conn, """
        SELECT r.reason, count(*) FROM audit_log a
        CROSS JOIN LATERAL jsonb_array_elements_text(a.policy_reasons) AS r(reason)
        WHERE a.record_type = 'Blocked' AND a.recorded_at >= :start AND a.recorded_at < :end
        GROUP BY 1""", params)
    return Policy(decisions, sum(blocked_by_event.values()), blocked_by_reason, blocked_by_event)


# --- compute: every group, one snapshot (design §5) --------------------------------------


@dataclass(frozen=True)
class Metrics:
    window: Window
    generated_at: datetime
    flow: Flow
    human_load: HumanLoad
    tools: Tools
    patient_sla: PatientSla
    policy: Policy


def compute(engine: Engine, window: Window, sources: Mapping[str, str | None]) -> Metrics:
    """All five groups in one REPEATABLE READ, READ ONLY transaction, so they agree with each
    other; STATEMENT_TIMEOUT bounds every query, and a timeout refuses the whole answer."""
    try:
        with engine.connect() as raw:
            conn = raw.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
            with conn.begin():
                conn.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
                generated_at = conn.execute(text("SELECT now()")).scalar_one()
                return Metrics(
                    window=window,
                    generated_at=generated_at,
                    flow=flow(conn, window),
                    human_load=human_load(conn, window),
                    tools=replace(tools(conn, window), sources=dict(sources)),
                    patient_sla=patient_sla(conn, window),
                    policy=policy(conn, window),
                )
    except OperationalError as exc:
        if isinstance(exc.orig, pg_errors.QueryCanceled):
            raise MetricsUnavailable("statement_timeout") from None
        raise
