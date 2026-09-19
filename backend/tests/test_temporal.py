"""Temporal Monitor: T1-T12 (spec §6.2) and the six deviations of §7, on synthetic traces.

Execution rows (TOOL_EXECUTION_STARTED / AUDIT_RECORDED) are recorded by the Tool
Executor from sub-project 3 on; here they are built by hand with the guard evidence
§6.1 says they carry.
"""
from datetime import UTC, datetime

import pytest

from hospital_agent.policy.temporal import TemporalMonitor, violations
from hospital_agent.repository import AuditEntry

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
EXEC_GUARDS = {"InPlan": True, "IdentityVerified": True, "PatientContextPresent": True, "AttemptsAvailable": True}


def row(event: str, state_after: str, *, guards=None, execution_id=None, content_hash=None,
        record_type="Transition") -> AuditEntry:
    return AuditEntry(case_id="CASE-1", patient_id="P-1", record_type=record_type, event=event, state_before=None,
                      state_after=state_after, rule_version="test", recorded_at=NOW, guards=guards or {},
                      execution_id=execution_id, content_hash=content_hash)


def allowed(e="E1", **guards) -> AuditEntry:
    return row("POLICY_ALLOWED", "RetrievingData", execution_id=e, guards=guards)


def started(e="E1", *, content_hash=None, **overrides) -> AuditEntry:
    return row("TOOL_EXECUTION_STARTED", "RetrievingData", execution_id=e, content_hash=content_hash,
               guards={**EXEC_GUARDS, **overrides}, record_type="ExecutionStarted")


def recorded(e="E1") -> AuditEntry:
    return row("AUDIT_RECORDED", "RetrievingData", execution_id=e)


def rules(trace) -> set[str]:
    return {v.rule for v in violations(trace)}


def test_a_clean_execution_satisfies_every_rule():
    assert rules([allowed(), started(), recorded(), row("DATA_RETRIEVED", "Planning")]) == set()


# --- one violating trace per rule -----------------------------------------------------


@pytest.mark.parametrize("rule, trace", [
    ("T1", [row("ACTION_PROPOSED", "Planning"), started(), recorded()]),
    ("T1", [allowed("E0"), started("E1"), recorded("E1")]),
    ("T2", [allowed(), started(InPlan=False), recorded()]),
    ("T3", [allowed(), started(IdentityVerified=False), recorded()]),
    ("T4", [allowed(), started(PatientContextPresent=False), recorded()]),
    ("T5", [row("MEDICAL_QUESTION_DETECTED", "Classified")]),
    ("T6", [allowed(), started(content_hash="H", medical_content_flag=True, ContentApprovalValid=True), recorded()]),
    ("T6", [allowed(ContentApprovalValid=True), started(content_hash="H", medical_content_flag=True), recorded()]),
    ("T7", [row("RETRY_EXHAUSTED", "Planning")]),
    ("T8", [row("READINESS_PASSED", "Ready")]),
    ("T9", [allowed(), started(), row("DATA_RETRIEVED", "Planning")]),
    ("T10", [row("DOCUMENT_UPLOADED", "AwaitingPatientInput", guards={"DocumentValid": True})]),
    ("T11", [row("HUMAN_RESOLVED_CASE", "Completed"), row("REQUEST_VALIDATED", "Classifying")]),
    ("T12", [allowed(), started(AttemptsAvailable=False), recorded()]),
])
def test_each_rule_catches_its_violation(rule, trace):
    assert rule in rules(trace)


def test_t6_holds_with_prior_authorization_for_the_same_message():
    trace = [row("POLICY_ALLOWED", "Delivering", execution_id="E1", content_hash="H",
                 guards={"ContentApprovalValid": True}),
             started(content_hash="H", medical_content_flag=True, ContentApprovalValid=True), recorded()]
    assert "T6" not in rules(trace)
    other_message = [trace[0], started(content_hash="OTHER", medical_content_flag=True, ContentApprovalValid=True)]
    assert "T6" in rules(other_message)


def test_satisfied_cases_of_the_state_rules():
    assert rules([row("MEDICAL_QUESTION_DETECTED", "AwaitingHumanReview"),
                  row("RETRY_EXHAUSTED", "AwaitingHumanReview"),
                  row("READINESS_PASSED", "Ready", guards={"ReadinessComplete": True}),
                  row("DOCUMENT_UPLOADED", "Classifying", guards={"DocumentValid": True}),
                  row("DOCUMENT_UPLOADED", "AwaitingPatientInput", guards={"!DocumentValid": True})]) == set()


def test_x_at_the_end_of_the_trace():
    """X of a terminal state holds at the end (it repeats); X after an execution is still pending."""
    assert rules([row("CASE_RESOLVED", "Completed")]) == set()
    assert rules([allowed(), started()]) == set()


def test_blocked_rows_are_not_part_of_the_trace():
    blocked = row("DATA_RETRIEVED", "RetrievingData", record_type="Blocked")
    assert rules([allowed(), started(), blocked, recorded()]) == set()


# --- the incremental check used by the State Manager --------------------------------------


def test_check_reports_only_a_new_violation():
    monitor = TemporalMonitor()
    assert monitor.check([allowed()], started()) is None
    assert monitor.check([allowed(), started()], row("DATA_RETRIEVED", "Planning")) == "T9"
    assert monitor.check([allowed(), started()], recorded()) is None
    already_bad = [row("READINESS_PASSED", "Ready")]
    assert monitor.check(already_bad, row("DELIVERY_PLANNED", "Planning")) is None


def test_check_ignores_blocked_candidates():
    assert TemporalMonitor().check([allowed(), started()], row("X", "Planning", record_type="Blocked")) is None


# --- §7: the six deviations the monitor must detect in the demo -------------------------


@pytest.mark.parametrize("deviation, trace, candidate, rule", [
    ("tool call before policy approval", [row("ACTION_PROPOSED", "Planning")], started(), "T1"),
    ("medical answer without valid human approval", [allowed()],
     started(content_hash="H", medical_content_flag=True), "T6"),
    ("execution after the cycle's attempts ran out", [allowed()], started(AttemptsAvailable=False), "T12"),
    ("attempts exhausted without escalating", [allowed(), started(), recorded()],
     row("RETRY_EXHAUSTED", "Planning"), "T7"),
    ("execution that wrote no audit record", [allowed(), started()], row("DATA_RETRIEVED", "Planning"), "T9"),
])
def test_spec_7_deviations(deviation, trace, candidate, rule):
    assert TemporalMonitor().check(trace, candidate) == rule
    # the sixth deviation, "a State change without a matching Event", is the State Manager's
    # §3 table check: tests/test_fsm.py and tests/test_state_manager.py (guard_failed).
