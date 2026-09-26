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


def test_effects_record_the_new_appointment_facts_from_check_appointment_only():
    """Sub-project 18 (D6): CheckAppointment's facts are stored on RECORD_RETRIEVAL from its own
    answer, and a later retrieval at another step (CheckDocuments, LoadInstructions) leaves them
    untouched. answered_appointment_id (fix round 1, I2) is the service's own echo of the
    appointment it resolved - a separate column from appointment_id, which RECORD_RETRIEVAL
    never touches (see test_a_chosen_appointment_id_survives_data_retrieved). A second
    CheckAppointment answer replaces all six (final review M3; see
    test_a_second_check_appointment_answer_replaces_every_fact)."""
    retrieving = replace(new_case("CASE-1", "P-1", NOW), state=State.RETRIEVING_DATA,
                         ordered_steps=PLAN, current_step=1)
    row = _row(State.RETRIEVING_DATA, Event.DATA_RETRIEVED, State.PLANNING)
    payload = {"appointment_at": NOW, "required_documents": [], "answered_appointment_id": "APT-8391",
              "department": "Cardiology", "exam_type_label": "מבחן מאמץ",
              "instruction_source_id": "INSTR-CARD-STRESS", "instruction_version": "1", "upcoming_count": 2}
    after = apply_effects(retrieving, row, ctx(retrieving, Event.DATA_RETRIEVED, payload))
    assert after.answered_appointment_id == "APT-8391"
    assert after.department == "Cardiology"
    assert after.exam_type_label == "מבחן מאמץ"
    assert (after.instruction_source_id, after.instruction_version) == ("INSTR-CARD-STRESS", "1")
    assert after.upcoming_count == 2

    for step in (2, 3):  # CheckDocuments, LoadInstructions
        later = replace(after, current_step=step)
        again = apply_effects(later, row, ctx(later, Event.DATA_RETRIEVED, {"instruction_ids": ["x:1"]}))
        assert again.answered_appointment_id == "APT-8391" and again.upcoming_count == 2
        assert again.department == "Cardiology" and again.exam_type_label == "מבחן מאמץ"
        assert (again.instruction_source_id, again.instruction_version) == ("INSTR-CARD-STRESS", "1")


def test_a_second_check_appointment_answer_replaces_every_fact():
    """Final review M3: a CheckAppointment answer sets all six facts from itself - one that
    omits upcoming_count and department clears them to None rather than keeping the earlier
    answer's values. appointment_id, the patient's write-once choice, is untouched."""
    retrieving = replace(new_case("CASE-1", "P-1", NOW, appointment_id="APT-8391"),
                         state=State.RETRIEVING_DATA, ordered_steps=PLAN, current_step=1,
                         answered_appointment_id="APT-8391", department="Cardiology",
                         exam_type_label="מבחן מאמץ", instruction_source_id="INSTR-CARD-STRESS",
                         instruction_version="1", upcoming_count=2)
    row = _row(State.RETRIEVING_DATA, Event.DATA_RETRIEVED, State.PLANNING)
    payload = {"appointment_at": NOW, "required_documents": [], "answered_appointment_id": "APT-8391",
              "exam_type_label": "אקו לב", "instruction_source_id": "INSTR-CARD-ECHO",
              "instruction_version": "1"}  # no upcoming_count, no department
    after = apply_effects(retrieving, row, ctx(retrieving, Event.DATA_RETRIEVED, payload))
    assert after.upcoming_count is None
    assert after.department is None
    assert after.exam_type_label == "אקו לב"
    assert (after.instruction_source_id, after.instruction_version) == ("INSTR-CARD-ECHO", "1")
    assert after.answered_appointment_id == "APT-8391"
    assert after.appointment_id == "APT-8391"

    bare = apply_effects(after, row, ctx(after, Event.DATA_RETRIEVED, {"appointment_at": NOW}))
    assert (bare.answered_appointment_id, bare.department, bare.exam_type_label,
            bare.instruction_source_id, bare.instruction_version, bare.upcoming_count) == (None,) * 6
    assert bare.appointment_id == "APT-8391"


def test_a_check_appointment_with_no_instruction_clears_the_stale_source():
    """Fix round 1 (m4): a case that already resolved an instruction source (e.g. the patient's
    earlier chosen appointment) must not keep it once a later CheckAppointment answer no longer
    carries one - otherwise LoadInstructions would keep loading an approved text for an exam
    type/appointment the case no longer actually resolves to. Cleared, not merely left over.
    CheckDocuments'/LoadInstructions' own DATA_RETRIEVED at other steps must never clear it -
    only a retrieval while current_action is CheckAppointment does."""
    retrieving = replace(new_case("CASE-1", "P-1", NOW), state=State.RETRIEVING_DATA,
                         ordered_steps=PLAN, current_step=1,
                         instruction_source_id="INSTR-CARD-STRESS", instruction_version="1")
    row = _row(State.RETRIEVING_DATA, Event.DATA_RETRIEVED, State.PLANNING)
    payload = {"appointment_at": NOW, "required_documents": []}  # no "instruction" block at all
    after = apply_effects(retrieving, row, ctx(retrieving, Event.DATA_RETRIEVED, payload))
    assert (after.instruction_source_id, after.instruction_version) == (None, None)

    # CheckDocuments' own DATA_RETRIEVED (current_step=2) must never touch it.
    still_has_source = replace(retrieving, current_step=2)
    unaffected = apply_effects(still_has_source, row, ctx(still_has_source, Event.DATA_RETRIEVED,
                                                          {"held_documents": ["referral"]}))
    assert (unaffected.instruction_source_id, unaffected.instruction_version) == ("INSTR-CARD-STRESS", "1")


def test_a_chosen_appointment_id_survives_data_retrieved():
    """Fix round 1 (I2): appointment_id is write-once from REQUEST_SUBMITTED - RECORD_RETRIEVAL
    must never overwrite it, even when the service's own answered_appointment_id differs (a
    round-trip mismatch is refused earlier, by map_response's I1 check; this only proves the
    FSM effect itself is incapable of touching the column at all)."""
    retrieving = replace(new_case("CASE-1", "P-1", NOW, appointment_id="APT-8391"),
                         state=State.RETRIEVING_DATA, ordered_steps=PLAN, current_step=1)
    row = _row(State.RETRIEVING_DATA, Event.DATA_RETRIEVED, State.PLANNING)
    payload = {"appointment_at": NOW, "required_documents": [], "answered_appointment_id": "SERVICE-ANSWERED"}
    after = apply_effects(retrieving, row, ctx(retrieving, Event.DATA_RETRIEVED, payload))
    assert after.appointment_id == "APT-8391"
    assert after.answered_appointment_id == "SERVICE-ANSWERED"


def test_valid_upload_is_held_once():
    waiting = replace(new_case("CASE-1", "P-1", NOW), state=State.AWAITING_PATIENT_INPUT, held_documents=["referral"])
    row = _row(State.AWAITING_PATIENT_INPUT, Event.DOCUMENT_UPLOADED, State.CLASSIFYING)
    payload = {"document": {"document_id": "blood_test"}}
    after = apply_effects(waiting, row, ctx(waiting, Event.DOCUMENT_UPLOADED, payload))
    assert after.held_documents == ["referral", "blood_test"]
    again = apply_effects(after, row, ctx(after, Event.DOCUMENT_UPLOADED, payload))
    assert again.held_documents == ["referral", "blood_test"]
