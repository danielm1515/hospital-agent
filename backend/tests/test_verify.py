"""verify_start - the Tool Executor's re-verification right before a call (spec §3.1 ExecutorReverified)."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from hospital_agent.case import CaseRecord, ExecutionRecord, compute_plan_hash
from hospital_agent.execution.verify import REVERIFICATION_FAILED, verify_start
from hospital_agent.naming import State

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
PLAN = [
    {"step": 1, "action": "CheckAppointment"},
    {"step": 2, "action": "CheckDocuments"},
    {"step": 3, "action": "LoadInstructions"},
    {"step": 4, "action": "SendStatusUpdate"},
]


def _case(**changes) -> CaseRecord:
    base = CaseRecord(case_id="CASE-1", patient_id="P-10041", state=State.RETRIEVING_DATA, state_version=7,
                      identity_verified=True, ordered_steps=PLAN, plan_hash=compute_plan_hash(PLAN),
                      current_step=2, attempt_count=1, created_at=NOW, updated_at=NOW)
    return replace(base, **changes)


def _execution(**changes) -> ExecutionRecord:
    base = ExecutionRecord(execution_id="EXEC-1", case_id="CASE-1", patient_id="P-10041", action="CheckDocuments",
                           step=2, retry_cycle=0, attempt_number=2, idempotency_key="CASE-1:2:0:2", status="intent",
                           decision_token="tok", state_version=7, plan_hash=compute_plan_hash(PLAN))
    return replace(base, **changes)


def test_verify_start_accepts_an_untouched_decision():
    assert verify_start(_case(), _execution(), None, NOW) is None


@pytest.mark.parametrize("case_change, execution_change", [
    ({}, {"status": "started"}),                       # already started - never replayed
    ({"state_version": 8}, {}),                        # the case moved on after the decision
    ({"current_step": 3}, {}),
    ({"state": State.PLANNING}, {}),
    ({"identity_verified": False}, {}),
    ({}, {"plan_hash": "0" * 64}),
    ({}, {"action": "LoadInstructions"}),
    ({}, {"patient_id": "P-OTHER"}),
    ({}, {"decision_token": ""}),
    ({}, {"idempotency_key": " "}),
])
def test_verify_start_rejects_any_drift(case_change, execution_change):
    assert verify_start(_case(**case_change), _execution(**execution_change), None, NOW) == REVERIFICATION_FAILED


def test_verify_start_rejects_a_missing_row_and_an_unapproved_medical_message():
    assert verify_start(_case(), None, None, NOW) == REVERIFICATION_FAILED
    medical = _execution(medical_content_flag=True, approval_id="APPR-X", content_hash="H")
    assert verify_start(_case(), medical, None, NOW) is not None
