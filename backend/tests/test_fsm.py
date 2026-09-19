"""The transition table must be exactly spec §3, and resolve() must follow its rules."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from hospital_agent.case import compute_plan_hash, new_case
from hospital_agent.fsm import TRANSITIONS, AmbiguousTransition, Transition, apply_effects, resolve
from hospital_agent.guards import GUARDS, GuardContext
from hospital_agent.naming import RESUMABLE, EscalationKind, Event, State
from tests.fakes import fake_ports
from tests.spec_tables import read_table

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
PLAN = [
    {"step": 1, "action": "CheckAppointment"},
    {"step": 2, "action": "CheckDocuments"},
    {"step": 3, "action": "LoadInstructions"},
    {"step": 4, "action": "SendStatusUpdate"},
]


def ctx(case, event, payload=None, **kw):
    return GuardContext(case=case, event=event, payload=payload or {}, now=NOW, ports=fake_ports(), **kw)


def test_table_matches_spec_3_row_by_row():
    spec_rows = read_table("03-transitions-guards.md", ("מצב נוכחי", "Event", "Guard", "מצב הבא"))
    ours = [[t.source.value if t.source else "Initial", t.event.value, t.spec_guard, t.target.value] for t in TRANSITIONS]
    assert ours == spec_rows
    assert len(TRANSITIONS) == 41


def test_every_guard_name_exists_and_appears_in_its_spec_cell():
    for row in TRANSITIONS:
        for name in row.guards:
            base = name.removeprefix("!")
            assert base in GUARDS, base
            if base[0].isupper():
                assert base in row.spec_guard, (row.event, base)


def test_escalation_kinds_appear_in_their_spec_cell_and_cover_all_14():
    seen = set()
    for row in TRANSITIONS:
        if row.escalation:
            for kind in row.escalation.kinds:
                assert kind.value in row.spec_guard, (row.event, kind)
            seen |= row.escalation.kinds
    assert seen == set(EscalationKind)


def test_human_approved_resumes_exactly_the_resumable_kinds():
    kinds = set()
    for row in TRANSITIONS:
        if row.event is Event.HUMAN_APPROVED:
            kinds |= row.requires_escalation
    assert kinds == set(RESUMABLE)


def test_terminal_states_have_no_outgoing_rows():
    assert not [t for t in TRANSITIONS if t.source in {State.COMPLETED, State.FAILED}]


def test_no_row_means_guard_failed():
    res = resolve(State.COMPLETED, Event.HUMAN_APPROVED, ctx(None, Event.HUMAN_APPROVED))
    assert (res.transition, res.reason) == (None, "guard_failed")


def test_negated_guard_picks_between_rows():
    classified_payload = {"safety_level": "MediumRisk"}
    classifying = replace(new_case("CASE-1", "P-1", NOW), state=State.CLASSIFYING)
    assert resolve(
        State.CLASSIFYING, Event.INTENT_CLASSIFIED, ctx(classifying, Event.INTENT_CLASSIFIED, classified_payload)
    ).transition.target is State.CLASSIFIED
    in_progress = replace(classifying, plan_hash=compute_plan_hash(PLAN), ordered_steps=PLAN, required_documents=["x"])
    res = resolve(State.CLASSIFYING, Event.INTENT_CLASSIFIED, ctx(in_progress, Event.INTENT_CLASSIFIED, classified_payload))
    assert res.transition.target is State.ASSESSING_READINESS
    assert res.guard_results == {"valid_classification": True, "ReadinessInProgress": True}


def test_specific_reason_wins_over_guard_failed():
    case = replace(
        new_case("CASE-1", "P-1", NOW),
        state=State.AWAITING_HUMAN_REVIEW,
        escalation_kind=EscalationKind.PATIENT_VERIFICATION_FAILED,
    )
    res = resolve(State.AWAITING_HUMAN_REVIEW, Event.HUMAN_APPROVED, ctx(case, Event.HUMAN_APPROVED))
    assert res.reason == "workflow_decision_invalid"  # no approval loaded at all


def test_two_matching_rows_fail_closed(monkeypatch):
    duplicate = Transition(State.PLANNING, Event.ACTION_PROPOSED, State.PLANNING, "InPlan, PlanIntact")
    monkeypatch.setattr("hospital_agent.fsm.TRANSITIONS", (duplicate, duplicate))
    case = replace(new_case("CASE-1", "P-1", NOW), state=State.PLANNING)
    with pytest.raises(AmbiguousTransition):
        resolve(State.PLANNING, Event.ACTION_PROPOSED, ctx(case, Event.ACTION_PROPOSED))


def _row(source, event, target) -> Transition:
    return next(t for t in TRANSITIONS if (t.source, t.event, t.target) == (source, event, target))


def test_effects_record_the_plan_and_advance_steps():
    classified = replace(new_case("CASE-1", "P-1", NOW), state=State.CLASSIFIED)
    row = _row(State.CLASSIFIED, Event.PLAN_CREATED, State.PLANNING)
    planned = apply_effects(classified, row, ctx(classified, Event.PLAN_CREATED, {"ordered_steps": PLAN}))
    assert (planned.state, planned.current_step, planned.plan_hash) == (State.PLANNING, 1, compute_plan_hash(PLAN))

    stepped = apply_effects(
        replace(planned, attempt_count=2, retry_cycle=1),
        _row(State.PLANNING, Event.STEP_ADVANCED, State.PLANNING),
        ctx(planned, Event.STEP_ADVANCED),
    )
    assert (stepped.current_step, stepped.attempt_count, stepped.retry_cycle) == (2, 0, 0)


def test_effects_record_escalation_and_clear_it_on_resume():
    planning = replace(new_case("CASE-1", "P-1", NOW), state=State.RETRIEVING_DATA, attempt_count=3)
    row = _row(State.RETRIEVING_DATA, Event.RETRY_EXHAUSTED, State.AWAITING_HUMAN_REVIEW)
    waiting = apply_effects(planning, row, ctx(planning, Event.RETRY_EXHAUSTED))
    assert (waiting.escalation_kind, waiting.escalated_from_state) == (EscalationKind.RETRY_EXHAUSTED, State.RETRIEVING_DATA)

    resume = next(t for t in TRANSITIONS if t.requires_escalation == frozenset({EscalationKind.RETRY_EXHAUSTED}))
    resumed = apply_effects(waiting, resume, ctx(waiting, Event.HUMAN_APPROVED))
    assert (resumed.state, resumed.retry_cycle, resumed.attempt_count) == (State.PLANNING, 1, 0)
    assert resumed.escalation_kind is None and resumed.escalated_from_state is None


def test_signal_rows_take_the_kind_from_the_payload():
    classifying = replace(new_case("CASE-1", "P-1", NOW), state=State.CLASSIFYING)
    row = _row(State.CLASSIFYING, Event.HUMAN_REVIEW_REQUIRED, State.AWAITING_HUMAN_REVIEW)
    payload = {"escalation_kind": "SafetyEscalation", "escalated_from_state": "Classifying"}
    after = apply_effects(classifying, row, ctx(classifying, Event.HUMAN_REVIEW_REQUIRED, payload))
    assert after.escalation_kind is EscalationKind.SAFETY_ESCALATION


def test_valid_upload_is_held_once():
    waiting = replace(new_case("CASE-1", "P-1", NOW), state=State.AWAITING_PATIENT_INPUT, held_documents=["referral"])
    row = _row(State.AWAITING_PATIENT_INPUT, Event.DOCUMENT_UPLOADED, State.CLASSIFYING)
    payload = {"document": {"document_id": "blood_test"}}
    after = apply_effects(waiting, row, ctx(waiting, Event.DOCUMENT_UPLOADED, payload))
    assert after.held_documents == ["referral", "blood_test"]
    again = apply_effects(after, row, ctx(after, Event.DOCUMENT_UPLOADED, payload))
    assert again.held_documents == ["referral", "blood_test"]
