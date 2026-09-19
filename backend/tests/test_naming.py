"""The closed name lists must match the spec tables exactly (§2.1, §2.2, §5, §13.2)."""
import pytest

from hospital_agent.naming import (
    AUTOMATIC_ACTIONS,
    EVENT_OWNER,
    EXTERNAL_EVENTS,
    NON_TRANSITION_EVENTS,
    RESUMABLE,
    Action,
    Component,
    DeprecatedEventName,
    Event,
    State,
    UnknownName,
    canonical_event,
    to_pascal,
    to_prolog,
)
from tests.spec_tables import read_table


def test_states_match_spec_2_1():
    rows = read_table("02-states-events.md", ("State", "משמעות", "תרחישים"))
    assert [s.value for s in State] == [row[0] for row in rows]
    assert len(State) == 12


def test_events_match_spec_2_2_in_order():
    rows = read_table("02-states-events.md", ("#", "Event", "משמעות"))
    assert [e.value for e in Event] == [row[1] for row in rows]
    assert len(Event) == 26


def test_actions_match_spec_5():
    rows = read_table("05-action-registry.md", ("Action", "מבצע", "Risk", "Human", "Precondition", "Postcondition"))
    assert [a.value for a in Action] == [row[0] for row in rows]
    automatic = {row[0] for row in rows if row[1] == "ToolExecutor"}
    assert {a.value for a in AUTOMATIC_ACTIONS} == automatic


def test_prolog_names_match_spec_5_mapping_both_ways():
    rows = read_table("05-action-registry.md", ("OPA / JSON / State", "Prolog / Datalog"))
    for pascal, prolog in rows:
        assert to_prolog(pascal) == prolog
        assert to_pascal(prolog) is Action(pascal)
    with pytest.raises(UnknownName):
        to_pascal("answer_clinical_question")


SPEC_OWNER_LABELS = {
    "Session Service": {Component.SESSION_SERVICE},
    "Classifier Service": {Component.CLASSIFIER_SERVICE},
    "Planner Service": {Component.PLANNER_SERVICE},
    "Policy Service": {Component.POLICY_SERVICE},
    "Tool Executor": {Component.TOOL_EXECUTOR},
    "Readiness Check": {Component.READINESS_CHECK},
    "Agent Orchestrator / Escalation Coordinator": {Component.AGENT_ORCHESTRATOR, Component.ESCALATION_COORDINATOR},
    "SLA / Timer Worker": {Component.SLA_WORKER},
    "Response Delivery": {Component.RESPONSE_DELIVERY},
    "State Manager": {Component.STATE_MANAGER},
}


def test_event_owners_match_spec_13_2():
    rows = read_table("13-invariants.md", ("Event", "מי מייצר אותו"))
    seen = set()
    for events_cell, owner_cell in rows:
        label = next(k for k in sorted(SPEC_OWNER_LABELS, key=len, reverse=True) if owner_cell.startswith(k))
        for name in events_cell.split(", "):
            assert EVENT_OWNER[Event(name)] in SPEC_OWNER_LABELS[label], name
            seen.add(Event(name))
    assert seen == set(EVENT_OWNER)
    assert len(EVENT_OWNER) == 21


def test_human_review_required_is_owned_by_the_escalation_coordinator_only():
    assert EVENT_OWNER[Event.HUMAN_REVIEW_REQUIRED] is Component.ESCALATION_COORDINATOR


def test_external_events_are_the_patient_and_the_three_human_decisions():
    assert EXTERNAL_EVENTS == {
        Event.REQUEST_SUBMITTED,
        Event.DOCUMENT_UPLOADED,
        Event.HUMAN_APPROVED,
        Event.HUMAN_REJECTED,
        Event.HUMAN_RESOLVED_CASE,
    }
    assert NON_TRANSITION_EVENTS == {Event.TOOL_EXECUTION_STARTED, Event.AUDIT_RECORDED}


def test_resumable_escalations_and_their_required_fields():
    assert {k.value: v for k, v in RESUMABLE.items()} == {
        "PatientVerificationFailed": ("verified_identity_ref",),
        "RetryExhausted": (),
        "PolicyReview": ("plan_hash", "current_step"),
        "Z3Counterexample": ("patient_deadline",),
        "PatientSlaExpired": ("patient_deadline",),
    }


def test_canonical_event_accepts_canonical_names_only():
    assert canonical_event("REQUEST_SUBMITTED") is Event.REQUEST_SUBMITTED
    assert canonical_event(Event.CASE_RESOLVED) is Event.CASE_RESOLVED
    with pytest.raises(DeprecatedEventName, match="REQUEST_SUBMITTED"):
        canonical_event("SUBMITTED_REQUEST")
    with pytest.raises(UnknownName):
        canonical_event("REQUEST_CANCELLED")
