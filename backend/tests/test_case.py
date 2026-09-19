from dataclasses import replace
from datetime import UTC, datetime

from hospital_agent.case import compute_plan_hash, new_case
from hospital_agent.naming import Action, State

SPEC_PLAN = [
    {"step": 1, "action": "CheckAppointment"},
    {"step": 2, "action": "CheckDocuments"},
    {"step": 3, "action": "LoadInstructions"},
    {"step": 4, "action": "SendStatusUpdate"},
]
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def test_plan_hash_reproduces_the_spec_8_example():
    assert compute_plan_hash(SPEC_PLAN) == "70471a828372f98f7949666f569537f3bc213e64aa0d3ec84f55ec9e2ba8cb99"


def test_plan_hash_changes_when_the_plan_changes():
    modified = [*SPEC_PLAN[:3], {"step": 4, "action": "CheckAppointment"}]
    assert compute_plan_hash(modified) != compute_plan_hash(SPEC_PLAN)


def test_new_case_starts_in_received_at_version_1():
    case = new_case("CASE-1", "P-1", NOW)
    assert (case.state, case.state_version, case.identity_verified) == (State.RECEIVED, 1, False)
    assert case.held_documents == [] and case.required_documents is None


def test_step_navigation():
    case = new_case("CASE-1", "P-1", NOW)
    assert case.current_action is None and case.next_action is None
    planned = replace(case, ordered_steps=SPEC_PLAN, current_step=3)
    assert planned.current_action is Action.LOAD_INSTRUCTIONS
    assert planned.next_action is Action.SEND_STATUS_UPDATE
    assert replace(planned, current_step=4).next_action is None
    assert planned.step_action(0) is None
