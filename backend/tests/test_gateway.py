"""The mock external systems and the Retry Manager (Execution design §4)."""
import json
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent.case import CaseRecord
from hospital_agent.execution.gateway import (
    ACTION_TARGETS, ERROR, IDEMPOTENT_ACTIONS, INSTRUCTION_TEXT, OK, RESULT_FIELDS, TRANSIENT_FAILURE, MockGateway,
)
from hospital_agent.execution.retry import after_failure
from hospital_agent.naming import AUTOMATIC_ACTIONS, EscalationKind, Event, State
from hospital_agent.policy.build_minimized import OUTPUT as MINIMIZED_FIELDS

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


# --- gateway -------------------------------------------------------------------------------

def test_every_automatic_action_has_a_target_and_is_idempotent():
    assert set(ACTION_TARGETS) == {a.value for a in AUTOMATIC_ACTIONS} == IDEMPOTENT_ACTIONS


def test_every_action_has_result_fields():
    assert set(RESULT_FIELDS) == set(ACTION_TARGETS)


def test_action_parameters_are_within_the_minimized_fields():
    allowed = json.loads(MINIMIZED_FIELDS.read_text())["hospital_agent"]["minimized_fields"]
    for target, fields in ACTION_TARGETS.values():
        assert set(fields) <= set(allowed[target]), target


def test_mock_returns_the_demo_data():
    gw = MockGateway(clock=lambda: NOW)
    assert gw.call("CheckAppointment", {"patient_id": "P"}, "k1") == \
        type(gw.call("CheckAppointment", {}, "k0"))(OK, {"appointment_at": NOW + timedelta(hours=96)})
    docs = gw.call("CheckDocuments", {"patient_id": "P"}, "k2")
    assert docs.data == {"required_documents": ["referral", "blood_test"], "held_documents": ["referral"]}
    assert gw.call("LoadInstructions", {}, "k3").data == {"instruction_ids": ["INSTR-PREP-COLONOSCOPY:3"],
                                                          "instruction_text": INSTRUCTION_TEXT}


def test_mock_fails_as_scripted_then_recovers():
    gw = MockGateway(failures={"CheckDocuments": 2}, errors=frozenset({"LoadInstructions"}))
    kinds = [gw.call("CheckDocuments", {}, f"k{i}").kind for i in range(3)]
    assert kinds == [TRANSIENT_FAILURE, TRANSIENT_FAILURE, OK]
    assert gw.call("LoadInstructions", {}, "k").kind == ERROR
    assert [call[0] for call in gw.calls] == ["CheckDocuments"] * 3 + ["LoadInstructions"]


def test_patient_channel_ignores_a_repeated_idempotency_key():
    gw = MockGateway()
    gw.call("SendStatusUpdate", {"patient_id": "P", "content_hash": "H1"}, "same")
    gw.call("SendStatusUpdate", {"patient_id": "P", "content_hash": "H2"}, "same")
    assert gw.delivered == {"same": {"patient_id": "P", "content_hash": "H1"}}


def test_non_idempotent_can_be_scripted():
    gw = MockGateway(non_idempotent=frozenset({"CheckDocuments"}))
    assert not gw.idempotent("CheckDocuments") and gw.idempotent("CheckAppointment")


# --- Retry Manager -------------------------------------------------------------------------

def _case(attempt_count: int) -> CaseRecord:
    return CaseRecord(case_id="CASE-1", patient_id="P-10041", state=State.RETRIEVING_DATA, state_version=7,
                      created_at=NOW, updated_at=NOW, current_step=2, attempt_count=attempt_count)


@pytest.mark.parametrize("attempts, idempotent, transient, expected", [
    (1, True, True, Event.TOOL_TRANSIENT_FAILURE),
    (2, True, True, Event.TOOL_TRANSIENT_FAILURE),
    (3, True, True, Event.RETRY_EXHAUSTED),
    (1, False, True, EscalationKind.NON_IDEMPOTENT_FAILURE),   # D27
    (1, True, False, EscalationKind.NON_IDEMPOTENT_FAILURE),   # an error is not retried
])
def test_retry_verdict(attempts, idempotent, transient, expected):
    verdict = after_failure(_case(attempts), idempotent=idempotent, transient=transient)
    assert (verdict.event or verdict.escalation) is expected
