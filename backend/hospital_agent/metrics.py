"""The admin metrics screen's numbers (sub-project 14, design
docs/superpowers/specs/2026-09-24-admin-metrics-design.md).

Read-only. Every number aggregates tables the agent already writes - audit_log, executions,
cases and approvals - so a metric can never disagree with the Audit (design §3). compute()
runs every group in one REPEATABLE READ, READ ONLY transaction: all groups see the same
snapshot, and a slow query ends the whole answer (MetricsUnavailable), never a partial one
(design §5).

Two kinds of window (design §4.0): the cohort groups (flow, and sub-project 19's llm) count
the cases *opened* in [start, end); the event groups count what *happened* in it. Durations are
seconds; a percentile over no rows is None, never 0.

Sub-project 19 (design D6) adds the `llm` group - the LLM cost of the cohort, all of its
llm_usage rows whenever they were written - which compute() reads inside the same snapshot and
compute_llm() reads alone, for any staff member's GET /api/staff/llm-costs.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from psycopg import errors as pg_errors
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError

from .llm_costs import CallUsage, SourceUsage, money, total_cost

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

    @classmethod
    def parse(cls, start: str, end: str) -> Window:
        """The API's `from`/`to` query values: ISO-8601 instants, else InvalidWindow."""
        try:
            start_at, end_at = datetime.fromisoformat(start), datetime.fromisoformat(end)
        except ValueError:
            raise InvalidWindow("invalid_range") from None
        return cls(start_at, end_at)

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
    # Sub-project 15: a request to the patient is a staff action too. It is not a decision on the
    # escalation, so decided_by_kind (the approval join) leaves it out.
    decisions["PATIENT_REPLY_REQUESTED"] = int(conn.execute(text(f"""
        SELECT count(*) FROM audit_log
        WHERE record_type = 'Transition' AND event = 'PATIENT_REPLY_REQUESTED' AND {_EVENTS}"""),
        params).scalar_one())
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


# --- F: LLM cost (cohort - the cases opened in the window, all their usage; sub-project 19) --


@dataclass(frozen=True)
class LlmCosts:
    """Sub-project 19, design D6. Money follows llm_costs.total_cost's NULL rule. The averages
    divide by the cohort cases with at least one priced row (price_input_per_mtok IS NOT NULL):
    a case with no usage yet (not processed), or only an unknown model's, would otherwise pull
    the average down with a cost that is not 0 but unknown. None when there is no such case."""

    window: Window
    cases: int
    cases_with_usage: int
    calls: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_cost_usd: Decimal | None
    avg_cost_per_case_usd: Decimal | None
    avg_cost_per_completed_case_usd: Decimal | None
    unpriced_calls: int
    by_call: list[CallUsage]
    by_source: list[SourceUsage]


_USAGE_TOTALS = """count(*) AS calls, sum(u.cost_usd) AS cost,
    count(*) FILTER (WHERE u.price_input_per_mtok IS NULL) AS unpriced,
    coalesce(sum(u.input_tokens), 0) AS input_tokens,
    coalesce(sum(u.cached_input_tokens), 0) AS cached_input_tokens,
    coalesce(sum(u.output_tokens), 0) AS output_tokens"""
_COHORT_USAGE = f"FROM llm_usage u JOIN cases c ON c.case_id = u.case_id WHERE {_COHORT}"


def _average(total: Decimal, count: int) -> Decimal | None:
    return money(total / count) if count else None


def llm(conn: Connection, window: Window) -> LlmCosts:
    params = window.params
    # One row per cohort case (usage or not), then counted: every case, the ones with any row,
    # and the ones with a priced row - overall and among the Completed.
    cases, with_usage, priced_cases, priced_cost, priced_completed, completed_cost = conn.execute(text(f"""
        WITH per_case AS (
            SELECT c.state, count(u.usage_id) AS calls,
                   count(u.usage_id) FILTER (WHERE u.price_input_per_mtok IS NOT NULL) AS priced,
                   sum(u.cost_usd) AS cost
            FROM cases c LEFT JOIN llm_usage u ON u.case_id = c.case_id
            WHERE {_COHORT} GROUP BY c.case_id, c.state)
        SELECT count(*), count(*) FILTER (WHERE calls > 0),
               count(*) FILTER (WHERE priced > 0), coalesce(sum(cost) FILTER (WHERE priced > 0), 0),
               count(*) FILTER (WHERE priced > 0 AND state = 'Completed'),
               coalesce(sum(cost) FILTER (WHERE priced > 0 AND state = 'Completed'), 0)
        FROM per_case"""), params).one()
    totals = conn.execute(text(f"SELECT {_USAGE_TOTALS} {_COHORT_USAGE}"), params).one()
    by_call = [CallUsage(row.call, row.calls, row.input_tokens, row.output_tokens,
                         total_cost(row.cost, row.calls, row.unpriced))
               for row in conn.execute(text(f"SELECT u.call, {_USAGE_TOTALS} {_COHORT_USAGE} GROUP BY 1 ORDER BY 1"),
                                       params)]
    by_source = [SourceUsage(row.source, row.calls, total_cost(row.cost, row.calls, row.unpriced))
                 for row in conn.execute(text(f"SELECT u.source, {_USAGE_TOTALS} {_COHORT_USAGE} "
                                              "GROUP BY 1 ORDER BY 1"), params)]
    return LlmCosts(
        window=window, cases=int(cases), cases_with_usage=int(with_usage), calls=int(totals.calls),
        input_tokens=int(totals.input_tokens), cached_input_tokens=int(totals.cached_input_tokens),
        output_tokens=int(totals.output_tokens),
        total_cost_usd=total_cost(totals.cost, totals.calls, totals.unpriced),
        avg_cost_per_case_usd=_average(priced_cost, priced_cases),
        avg_cost_per_completed_case_usd=_average(completed_cost, priced_completed),
        unpriced_calls=int(totals.unpriced), by_call=by_call, by_source=by_source)


# --- G: success metrics (the presentation's three) ---------------------------------------
#
# A case's appointment is the one the appointment service answered about, else the one the
# patient chose: (patient_id, that id) is "one appointment" for readiness and repeat requests.
# A case with neither is left out of both, and counted on its own in no_appointment.

_APPOINTMENT = "COALESCE(c.answered_appointment_id, c.appointment_id)"


@dataclass(frozen=True)
class Readiness:
    """Appointments whose time fell in the window and has passed: `ready` had a case resolved
    (CASE_RESOLVED - documents checked, instructions delivered) before it. Appointments still
    ahead, at any date, are `upcoming` - not judged yet."""

    judged: int
    ready: int
    rate: float | None
    upcoming: int
    upcoming_ready: int


@dataclass(frozen=True)
class HandlingTime:
    """The cases opened in the window: from opening to the first of a hand-off to staff (entering
    AwaitingHumanReview) or an end (Completed / Failed). `open` reached neither yet."""

    overall: Durations
    closed: Durations
    handed_off: Durations
    open: int


@dataclass(frozen=True)
class RepeatRequests:
    """The cases opened in the window, per appointment: every case after the first is a repeat."""

    appointments: int
    repeat_requests: int
    appointments_with_repeats: int
    max_requests: int
    no_appointment: int


@dataclass(frozen=True)
class Success:
    readiness: Readiness
    handling_time: HandlingTime
    repeat_requests: RepeatRequests


_READINESS = f"""
    WITH appointments AS (
        SELECT c.patient_id, {_APPOINTMENT} AS appointment, max(c.appointment_at) AS appointment_at,
               bool_or(EXISTS (SELECT 1 FROM audit_log a WHERE a.case_id = c.case_id
                                 AND a.record_type = 'Transition' AND a.event = 'CASE_RESOLVED'
                                 AND a.recorded_at < c.appointment_at)) AS ready
        FROM cases c
        WHERE {_APPOINTMENT} IS NOT NULL AND c.appointment_at IS NOT NULL
        GROUP BY 1, 2),
    judged AS (
        SELECT *, appointment_at >= :start AND appointment_at < :end AND appointment_at < now() AS judged
        FROM appointments)
    SELECT count(*) FILTER (WHERE judged), count(*) FILTER (WHERE judged AND ready),
           count(*) FILTER (WHERE appointment_at >= now()),
           count(*) FILTER (WHERE appointment_at >= now() AND ready)
    FROM judged"""

# One row per case opened in the window: how its handling ended ('open' when it has not) and
# how long it took.
_HANDLING_KINDS = f"""
    WITH ends AS (
        SELECT c.created_at,
               (SELECT min(a.recorded_at) FROM audit_log a WHERE a.case_id = c.case_id
                  AND a.record_type = 'Transition' AND a.state_after = 'AwaitingHumanReview') AS handed_off_at,
               (SELECT min(a.recorded_at) FROM audit_log a WHERE a.case_id = c.case_id
                  AND a.record_type = 'Transition' AND a.state_after IN ('Completed', 'Failed')) AS closed_at
        FROM cases c WHERE {_COHORT}),
    kinds AS (
        SELECT CASE WHEN handed_off_at IS NULL AND closed_at IS NULL THEN 'open'
                    WHEN closed_at IS NULL OR handed_off_at < closed_at THEN 'handed_off'
                    ELSE 'closed' END AS kind,
               LEAST(handed_off_at, closed_at) - created_at AS took
        FROM ends)"""

_REPEATS = f"""
    SELECT count(*), COALESCE(sum(n - 1), 0), count(*) FILTER (WHERE n > 1), COALESCE(max(n), 0),
           (SELECT count(*) FROM cases c WHERE {_COHORT} AND {_APPOINTMENT} IS NULL)
    FROM (SELECT count(*) AS n FROM cases c WHERE {_COHORT} AND {_APPOINTMENT} IS NOT NULL
          GROUP BY c.patient_id, {_APPOINTMENT}) per_appointment"""


def success(conn: Connection, window: Window) -> Success:
    judged, ready, upcoming, upcoming_ready = conn.execute(text(_READINESS), window.params).one()
    readiness = Readiness(judged=judged, ready=ready, rate=ready / judged if judged else None,
                          upcoming=upcoming, upcoming_ready=upcoming_ready)

    by_kind = {row[0]: _durations(row[1:]) for row in conn.execute(text(
        f"{_HANDLING_KINDS} SELECT kind, {_durations_sql('took')} FROM kinds GROUP BY kind"), window.params)}
    overall = conn.execute(text(
        f"{_HANDLING_KINDS} SELECT {_durations_sql('took')} FROM kinds WHERE kind <> 'open'"), window.params).one()
    handling = HandlingTime(overall=_durations(overall),
                            closed=by_kind.get("closed", EMPTY_DURATIONS),
                            handed_off=by_kind.get("handed_off", EMPTY_DURATIONS),
                            open=by_kind["open"].count if "open" in by_kind else 0)

    appointments, repeats, with_repeats, most, none = conn.execute(text(_REPEATS), window.params).one()
    return Success(readiness=readiness, handling_time=handling, repeat_requests=RepeatRequests(
        appointments=int(appointments), repeat_requests=int(repeats),
        appointments_with_repeats=int(with_repeats), max_requests=int(most), no_appointment=int(none)))


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
    llm: LlmCosts
    success: Success


@contextmanager
def _snapshot(engine: Engine) -> Iterator[Connection]:
    """One REPEATABLE READ, READ ONLY transaction with STATEMENT_TIMEOUT on every query; a
    timeout is MetricsUnavailable - the whole answer is refused, never a partial one."""
    try:
        with engine.connect() as raw:
            conn = raw.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
            with conn.begin():
                # Per statement, not per transaction - compute() runs about 18 of them, so the
                # whole call can take longer than STATEMENT_TIMEOUT while still bounding each
                # query individually. Postgres SET takes no bind parameters, which is why
                # STATEMENT_TIMEOUT (a module constant, never request input) is interpolated here.
                conn.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
                yield conn
    except OperationalError as exc:
        if isinstance(exc.orig, pg_errors.QueryCanceled):
            raise MetricsUnavailable("statement_timeout") from None
        raise


def compute(engine: Engine, window: Window, sources: Mapping[str, str | None]) -> Metrics:
    """All seven groups in one snapshot (_snapshot), so they agree with each other."""
    with _snapshot(engine) as conn:
        generated_at = conn.execute(text("SELECT now()")).scalar_one()
        return Metrics(
            window=window,
            generated_at=generated_at,
            flow=flow(conn, window),
            human_load=human_load(conn, window),
            tools=replace(tools(conn, window), sources=dict(sources)),
            patient_sla=patient_sla(conn, window),
            policy=policy(conn, window),
            llm=llm(conn, window),
            success=success(conn, window),
        )


def compute_llm(engine: Engine, window: Window) -> LlmCosts:
    """The llm group alone (GET /api/staff/llm-costs, any staff), in the same kind of snapshot."""
    with _snapshot(engine) as conn:
        return llm(conn, window)
