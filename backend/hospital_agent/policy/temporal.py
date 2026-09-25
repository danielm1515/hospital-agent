"""Temporal Monitor - the twelve past-time rules of spec §6.2 over a case's audit trace. T13 is sub-project 15's extension (docs/spec_corrections.md row 84), not a §6.2 rule.

It implements the Core's TraceMonitor port: check(trace, candidate) runs before every
commit (§7) and returns the id of a rule the candidate would newly violate, or None.

Trace (§6.1, Policy design decision 1): the case's audit rows in order, without
Blocked rows (a blocked event is not a transition) and without the execution outcome
rows (Execution design decision 4). Each row is read as
(state_after, event, guards, execution_id, content_hash).

Operators (§6.1): Y = previous row (false on the first row), O = now or earlier,
X = next row. At the end of a trace X of a terminal state holds (the terminal state
repeats); X of any other state is still pending and is judged when the next row
arrives - which is exactly when check() sees it.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..repository import AuditEntry

# Policy design decision 1 + Execution design decision 4: a blocked event and a call's
# outcome record are not transitions of the trace.
EXCLUDED_RECORD_TYPES = frozenset({"Blocked", "ExecutionSucceeded", "ExecutionFailed", "ExecutionUnknown"})
TERMINAL = frozenset({"Completed", "Failed"})

Rows = Sequence[AuditEntry]


@dataclass(frozen=True)
class Violation:
    rule: str
    index: int  # position in the trace (excluded rows removed)


# --- atomic propositions (§6.1 table) ------------------------------------------------


def _guard(row: AuditEntry, name: str) -> bool:
    return row.guards.get(name) is True


def _execute(row: AuditEntry) -> bool:
    return row.event == "TOOL_EXECUTION_STARTED"


def _policy_allowed(row: AuditEntry, execution_id: str | None) -> bool:
    return row.event == "POLICY_ALLOWED" and row.execution_id == execution_id


def _human_review(row: AuditEntry) -> bool:
    return row.state_after == "AwaitingHumanReview"


def _human_authorized(row: AuditEntry, execution_id: str | None, content_hash: str | None) -> bool:
    return (row.event == "POLICY_ALLOWED" and _guard(row, "ContentApprovalValid")
            and row.execution_id == execution_id and row.content_hash == content_hash)


def _audit_recorded(row: AuditEntry, execution_id: str | None) -> bool:
    return row.event == "AUDIT_RECORDED" and row.execution_id == execution_id


def _next(rows: Rows, i: int, holds: Callable[[AuditEntry], bool], at_end: bool) -> bool:
    return holds(rows[i + 1]) if i + 1 < len(rows) else at_end


# --- the rules: each answers "does rule hold at position i?" -------------------------


def _t1(rows: Rows, i: int) -> bool:  # G(Execute(e) -> Y PolicyAllowed(e))
    r = rows[i]
    return not _execute(r) or (i > 0 and _policy_allowed(rows[i - 1], r.execution_id))


def _t2(rows: Rows, i: int) -> bool:  # G(Execute(e) -> InPlan)
    return not _execute(rows[i]) or _guard(rows[i], "InPlan")


def _t3(rows: Rows, i: int) -> bool:  # G(Execute(e) -> IdentityVerified)
    return not _execute(rows[i]) or _guard(rows[i], "IdentityVerified")


def _t4(rows: Rows, i: int) -> bool:  # G(Execute(e) -> PatientContextPresent)
    return not _execute(rows[i]) or _guard(rows[i], "PatientContextPresent")


def _t5(rows: Rows, i: int) -> bool:  # G(MEDICAL_QUESTION_DETECTED -> HumanReview)
    return rows[i].event != "MEDICAL_QUESTION_DETECTED" or _human_review(rows[i])


def _t6(rows: Rows, i: int) -> bool:  # G(MedicalAnswer(e,h) -> (ContentApprovalValid(e,h) & Y O HumanAuthorized(e,h)))
    r = rows[i]
    if not (_execute(r) and _guard(r, "medical_content_flag")):
        return True
    earlier = any(_human_authorized(rows[j], r.execution_id, r.content_hash) for j in range(i))
    return _guard(r, "ContentApprovalValid") and earlier


def _t7(rows: Rows, i: int) -> bool:  # G(RETRY_EXHAUSTED -> HumanReview)
    return rows[i].event != "RETRY_EXHAUSTED" or _human_review(rows[i])


def _t8(rows: Rows, i: int) -> bool:  # G(Ready -> ReadinessComplete)
    return rows[i].state_after != "Ready" or _guard(rows[i], "ReadinessComplete")


def _t9(rows: Rows, i: int) -> bool:  # G(Execute(e) -> X AuditRecorded(e))
    r = rows[i]
    return not _execute(r) or _next(rows, i, lambda n: _audit_recorded(n, r.execution_id), at_end=True)


def _t10(rows: Rows, i: int) -> bool:  # G((DOCUMENT_UPLOADED & DocumentValid) -> Classifying)
    r = rows[i]
    return not (r.event == "DOCUMENT_UPLOADED" and _guard(r, "DocumentValid")) or r.state_after == "Classifying"


def _t11(rows: Rows, i: int) -> bool:  # G(Terminal -> X Terminal)
    return rows[i].state_after not in TERMINAL or _next(rows, i, lambda n: n.state_after in TERMINAL, at_end=True)


def _t12(rows: Rows, i: int) -> bool:  # G(Execute(e) -> AttemptsAvailable)
    return not _execute(rows[i]) or _guard(rows[i], "AttemptsAvailable")


# Sub-project 15 (docs/spec_corrections.md row 84): the states in which an AI component or the
# Tool Executor acts. A case a person has written to (PATIENT_REPLY_REQUESTED) never enters one.
AGENT_STATES = frozenset({"Classifying", "Classified", "Planning", "RetrievingData", "Delivering",
                          "AssessingReadiness", "Ready"})


def _t13(rows: Rows, i: int) -> bool:  # extension: G(AgentState -> ¬ O PATIENT_REPLY_REQUESTED)
    return rows[i].state_after not in AGENT_STATES or not any(
        r.event == "PATIENT_REPLY_REQUESTED" for r in rows[: i + 1])


RULES: tuple[tuple[str, Callable[[Rows, int], bool]], ...] = (
    ("T1", _t1), ("T2", _t2), ("T3", _t3), ("T4", _t4), ("T5", _t5), ("T6", _t6),
    ("T7", _t7), ("T8", _t8), ("T9", _t9), ("T10", _t10), ("T11", _t11), ("T12", _t12),
    ("T13", _t13),
)


def trace_rows(entries: Rows) -> list[AuditEntry]:
    return [e for e in entries if e.record_type not in EXCLUDED_RECORD_TYPES]


def violations(entries: Rows) -> list[Violation]:
    """Every (rule, position) that does not hold on this trace."""
    rows = trace_rows(entries)
    return [Violation(rule, i) for i in range(len(rows)) for rule, holds in RULES if not holds(rows, i)]


class TemporalMonitor:
    """The TraceMonitor port of the State Manager (Core design §7)."""

    def check(self, trace: Rows, candidate: AuditEntry) -> str | None:
        if candidate.record_type in EXCLUDED_RECORD_TYPES:
            return None
        before = set(violations(trace))
        for violation in violations([*trace, candidate]):
            if violation not in before:
                return violation.rule
        return None
