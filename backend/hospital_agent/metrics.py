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
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

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
