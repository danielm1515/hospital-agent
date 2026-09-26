"""The mock external systems and the Retry Manager (Execution design §4)."""
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent.case import CaseRecord
from hospital_agent.execution.gateway import (
    ACTION_TARGETS,
    ERROR,
    IDEMPOTENT_ACTIONS,
    INSTRUCTION_TEXT,
    OK,
    RESULT_FIELDS,
    TRANSIENT_FAILURE,
    MockGateway,
    present_patient_fields,
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


APPOINTMENT_DEMO_DATA = {"appointment_at": NOW + timedelta(hours=96), "required_documents": ["referral", "blood_test"],
                         "instruction_source_id": "INSTR-PREP-COLONOSCOPY", "instruction_version": "3"}


def test_mock_returns_the_demo_data():
    gw = MockGateway(clock=lambda: NOW)
    assert gw.call("CheckAppointment", {"patient_id": "P"}, "k1") == \
        type(gw.call("CheckAppointment", {}, "k0"))(OK, APPOINTMENT_DEMO_DATA)
    docs = gw.call("CheckDocuments", {"patient_id": "P"}, "k2")
    assert docs.data == {"held_documents": ["referral"]}
    assert gw.call("LoadInstructions", {}, "k3").data == {"instruction_ids": ["INSTR-PREP-COLONOSCOPY:3"],
                                                          "instruction_text": INSTRUCTION_TEXT}


def test_the_mock_appointment_carries_the_requirements_and_the_mock_documents_only_what_is_held():
    gw = MockGateway(clock=lambda: NOW)
    assert gw.call("CheckAppointment", {"patient_id": "P"}, "k").data == APPOINTMENT_DEMO_DATA
    assert gw.call("CheckDocuments", {"patient_id": "P"}, "k").data == {"held_documents": ["referral"]}


def test_the_mock_carries_the_demo_colonoscopy_instruction_source():
    """Sub-project 18 (D7): a case without a real appointment-service still resolves to the
    demo colonoscopy instructions, so the golden traces (§0, §15) keep loading them unchanged."""
    gw = MockGateway(clock=lambda: NOW)
    data = gw.call("CheckAppointment", {"patient_id": "P"}, "k").data
    assert (data["instruction_source_id"], data["instruction_version"]) == ("INSTR-PREP-COLONOSCOPY", "3")


def test_each_system_supplies_only_its_own_facts():
    """Design §5.1: the appointment system owns the requirements, the document system what is held."""
    assert RESULT_FIELDS["CheckAppointment"] == ("appointment_at", "required_documents", "appointment_id",
                                                  "department", "exam_type_label", "instruction_source_id",
                                                  "instruction_version", "upcoming_count")
    assert RESULT_FIELDS["CheckDocuments"] == ("held_documents",)


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


# --- present_patient_fields (sub-project 18, D5/D6) -----------------------------------------

def _case_without_appointment_id() -> CaseRecord:
    return CaseRecord(case_id="CASE-1", patient_id="P-10041", state=State.PLANNING, state_version=1,
                      created_at=NOW, updated_at=NOW)


def test_appointment_id_is_omitted_when_the_case_has_none():
    assert present_patient_fields(_case_without_appointment_id(), "CheckAppointment") == ("patient_id",)


def test_appointment_id_is_included_when_the_case_has_one():
    case = replace(_case_without_appointment_id(), appointment_id="APT-8391")
    assert present_patient_fields(case, "CheckAppointment") == ("patient_id", "appointment_id")


def test_fixed_fields_are_unaffected():
    case = _case_without_appointment_id()
    assert present_patient_fields(case, "CheckDocuments") == ("patient_id",)
    assert present_patient_fields(case, "SendStatusUpdate") == ("patient_id",)
    assert present_patient_fields(case, "LoadInstructions") == ()


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
