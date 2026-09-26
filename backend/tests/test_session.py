"""The Session Service (spec §1, §12.3, D24, D25; sub-project 5 design §4)."""
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent import data_log, repository
from hospital_agent import session as session_module
from hospital_agent.case import CaseRecord
from hospital_agent.execution.gateway import MockGateway
from hospital_agent.llm.orchestrator import Orchestrator
from hospital_agent.llm.provider import FakeProvider
from hospital_agent.naming import Component, EscalationKind, Event, SafetyLevel, State
from hospital_agent.session import (
    MISSING_DOCUMENT_TEMPLATE_ID,
    CaseNotFound,
    EventRejected,
    SessionService,
    patient_status,
)
from tests.driver import Driver

REQUEST = "When is my appointment and which documents do I need?"
PATIENT = "P-10041"


class Wake:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


@pytest.fixture
def wake():
    return Wake()


@pytest.fixture
def session(sm, wake):
    return SessionService(sm, wake=wake)


@pytest.fixture
def orchestrator(sm):
    agent = Orchestrator(sm, FakeProvider(), MockGateway())
    yield agent
    agent.close()


def request_texts(engine, case_id):
    with engine.connect() as conn:
        return [e.content for e in data_log.entries(conn, case_id, data_log.DataKind.REQUEST_TEXT)]


def uploads(engine, case_id):
    with engine.connect() as conn:
        return data_log.entries(conn, case_id, data_log.DataKind.UPLOADED_DOCUMENT)


# --- submitting --------------------------------------------------------------------------

def test_a_verified_patient_submits_and_the_case_is_classifying(session, sm, app_engine, wake):
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    assert sm.load(case_id).state is State.CLASSIFYING
    assert sm.load(case_id).patient_id == PATIENT
    assert request_texts(app_engine, case_id) == [REQUEST]
    assert wake.calls == 1


def test_a_chosen_appointment_id_is_stored_on_the_case(session, sm):
    """Sub-project 18 (D5): rides on REQUEST_SUBMITTED and is stored as the case is created."""
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True, appointment_id="APT-8391")
    assert sm.load(case_id).appointment_id == "APT-8391"


def test_without_a_chosen_appointment_the_case_has_none(session, sm):
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    assert sm.load(case_id).appointment_id is None


def test_an_unverified_patient_goes_to_review_and_the_text_is_kept(session, sm, app_engine):
    case_id = session.submit_request("P-30000", REQUEST, identity_verified=False)
    case = sm.load(case_id)
    assert (case.state, case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW,
                                                  EscalationKind.PATIENT_VERIFICATION_FAILED)
    assert request_texts(app_engine, case_id) == [REQUEST]


@pytest.mark.parametrize("text", ["", "   "])
def test_an_empty_submission_opens_no_case(session, sm, wake, text):
    with pytest.raises(EventRejected) as rejected:
        session.submit_request(PATIENT, text, identity_verified=True)  # RequestValid would block it
    assert rejected.value.reason == "request_text_required"
    assert session_cases(sm) == []  # no orphan case, and no empty Data Log row
    assert wake.calls == 0


def test_a_blocked_step_raises_event_rejected(session, sm, wake, monkeypatch):
    """A step the State Manager blocks: here REQUEST_VALIDATED without the request text."""
    monkeypatch.setattr(SessionService, "_validated",
                        lambda self, case_id, text, verified: self.sm.apply(
                            case_id, Event.REQUEST_VALIDATED, {"identity_verified": verified},
                            Component.SESSION_SERVICE))
    with pytest.raises(EventRejected) as rejected:
        session.submit_request(PATIENT, REQUEST, identity_verified=True)
    assert rejected.value.reason == "guard_failed"
    assert [case.state for case in session_cases(sm)] == [State.RECEIVED]
    assert wake.calls == 0


def session_cases(sm):
    with sm.engine.connect() as conn:
        return repository.list_cases(conn)


def test_revalidate_uses_the_stored_request_text_without_a_new_data_log_row(session, sm, app_engine):
    case_id = session.open_case(PATIENT).case_id
    with app_engine.begin() as conn:
        data_log.record(conn, case_id, PATIENT, data_log.DataKind.REQUEST_TEXT, REQUEST, sm.clock())
    result = session.revalidate(case_id)
    assert result.committed and result.state_after is State.CLASSIFYING
    assert request_texts(app_engine, case_id) == [REQUEST]


def test_revalidate_without_a_request_text_is_rejected(session):
    case_id = session.open_case(PATIENT).case_id
    with pytest.raises(EventRejected) as rejected:
        session.revalidate(case_id)
    assert rejected.value.reason == "request_text_unavailable"


# --- patient_status ------------------------------------------------------------------------

STATUS = {
    State.RECEIVED: "received",
    State.CLASSIFYING: "in_progress",
    State.CLASSIFIED: "in_progress",
    State.PLANNING: "in_progress",
    State.RETRIEVING_DATA: "in_progress",
    State.DELIVERING: "in_progress",
    State.ASSESSING_READINESS: "in_progress",
    State.AWAITING_PATIENT_INPUT: "needs_document",
    State.AWAITING_HUMAN_REVIEW: "in_review",
    State.READY: "in_progress",
    State.COMPLETED: "closed",  # without a CASE_RESOLVED row: closed by a reviewer
    State.FAILED: "closed",
    State.AWAITING_PATIENT_REPLY: "needs_reply",
}


def _record(state: State) -> CaseRecord:
    now = datetime.now(UTC)
    return CaseRecord("CASE-X", PATIENT, state, 1, now, now)


@pytest.mark.parametrize("state", list(State))
def test_patient_status_of_every_state(state):
    assert patient_status(_record(state), delivered=False) == STATUS[state]


def test_completed_is_completed_only_when_delivered():
    assert patient_status(_record(State.COMPLETED), delivered=True) == "completed"
    assert patient_status(_record(State.FAILED), delivered=True) == "closed"


# --- patient_view ----------------------------------------------------------------------------

def test_d24_needs_document_names_the_missing_documents_and_the_template(session, sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "consent", "blood_test"], held=["referral"])
    d.missing_information(z3_result="unsat")
    view = session.patient_view(d.case_id)
    assert view.status == "needs_document"
    assert view.missing_document_ids == ["blood_test", "consent"]
    assert view.missing_document_request_template_id == MISSING_DOCUMENT_TEMPLATE_ID == "missing-document-v1"
    assert view.message is None
    assert view.request_text == "When is my appointment and which documents do I need?"


def test_a_view_outside_needs_document_has_no_missing_documents(session):
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    view = session.patient_view(case_id)
    assert (view.status, view.missing_document_ids, view.missing_document_request_template_id) == (
        "in_progress", [], None)


def test_a_completed_case_shows_the_delivered_message(session, sm, app_engine, orchestrator):
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    assert orchestrator.run_case(case_id) is State.AWAITING_PATIENT_INPUT
    assert session.patient_view(case_id).missing_document_ids == ["blood_test"]
    session.upload_document(PATIENT, case_id, "blood_test", "Blood test results: normal.")
    assert orchestrator.run_case(case_id) is State.COMPLETED
    view = session.patient_view(case_id)
    with app_engine.connect() as conn:
        [message] = data_log.entries(conn, case_id, data_log.DataKind.OUTGOING_MESSAGE)
    assert (view.status, view.message) == ("completed", message.content)
    assert view.missing_document_ids == [] and view.missing_document_request_template_id is None


# --- sub-project 18 (design D11): the delivered instructions -----------------------------------

def test_a_completed_view_shows_the_instructions_that_were_loaded(session, sm, app_engine, orchestrator):
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    assert orchestrator.run_case(case_id) is State.AWAITING_PATIENT_INPUT
    session.upload_document(PATIENT, case_id, "blood_test", "Blood test results: normal.")
    assert orchestrator.run_case(case_id) is State.COMPLETED
    with app_engine.connect() as conn:
        [entry] = data_log.entries(conn, case_id, data_log.DataKind.INSTRUCTIONS)
    title, sep, text = entry.content.partition("\n")
    if not sep:  # fix round 1 (M5): no newline at all -> the generic title, whole entry as text
        title, text = "הוראות הכנה", title
    view = session.patient_view(case_id)
    assert view.instructions == session_module.PatientInstructions(title=title, text=text)


def test_instructions_is_null_outside_completed(session):
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    assert session.patient_view(case_id).instructions is None


def test_instructions_is_null_when_completed_with_no_instructions_entry(sm, app_engine):
    """The mock/pre-sub-project-18 path never wrote an `instructions` entry for some cases
    (e.g. a clinical answer, sub-project 8) - `None`, never an error."""
    d = Driver(sm, app_engine)
    d.submit()
    with app_engine.connect() as conn:
        assert SessionService._instructions(conn, d.case_id) is None


def test_instructions_with_no_newline_gets_the_generic_title(sm, app_engine):
    """Fix round 1 (M5): an entry with no newline at all gets the generic title "הוראות הכנה",
    and the whole entry becomes the text - never swallowed into the title."""
    d = Driver(sm, app_engine)
    d.submit()
    with app_engine.begin() as conn:
        data_log.record(conn, d.case_id, PATIENT, data_log.DataKind.INSTRUCTIONS,
                        "No newline anywhere in this entry", sm.clock())
        assert SessionService._instructions(conn, d.case_id) == session_module.PatientInstructions(
            title="הוראות הכנה", text="No newline anywhere in this entry")


def test_instructions_splits_only_on_the_first_newline(sm, app_engine):
    """Fix round 1 (M5): several newlines - only the first one splits title from text."""
    d = Driver(sm, app_engine)
    d.submit()
    with app_engine.begin() as conn:
        data_log.record(conn, d.case_id, PATIENT, data_log.DataKind.INSTRUCTIONS,
                        "Title\nLine one\nLine two\nLine three", sm.clock())
        assert SessionService._instructions(conn, d.case_id) == session_module.PatientInstructions(
            title="Title", text="Line one\nLine two\nLine three")


def test_instructions_shows_the_latest_present_entry_over_an_earlier_one(session, sm, app_engine, orchestrator):
    """Review m5: LoadInstructions can run more than once for one case (e.g. a re-plan after
    the patient uploads a document) - the LATEST entry wins, never the first."""
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    assert orchestrator.run_case(case_id) is State.AWAITING_PATIENT_INPUT
    session.upload_document(PATIENT, case_id, "blood_test", "Blood test results: normal.")
    assert orchestrator.run_case(case_id) is State.COMPLETED
    with app_engine.begin() as conn:
        data_log.record(conn, case_id, PATIENT, data_log.DataKind.INSTRUCTIONS,
                        "A newer title\nA newer text", sm.clock())
    view = session.patient_view(case_id)
    assert view.instructions == session_module.PatientInstructions(title="A newer title", text="A newer text")


def test_instructions_is_null_when_the_latest_entry_is_tombstoned(session, sm, app_engine, orchestrator):
    """Fix round 1 (I1/I2, gate d): a tombstoned LATEST entry is `null` - it never falls back
    to an older, still-present entry. A deletion must hide the text, not merely revert to a
    stale copy of it."""
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    assert orchestrator.run_case(case_id) is State.AWAITING_PATIENT_INPUT
    session.upload_document(PATIENT, case_id, "blood_test", "Blood test results: normal.")
    assert orchestrator.run_case(case_id) is State.COMPLETED
    with app_engine.begin() as conn:
        newer = data_log.record(conn, case_id, PATIENT, data_log.DataKind.INSTRUCTIONS,
                                "A newer title\nA newer text", sm.clock())
        data_log.tombstone(conn, newer.entry_id, sm.clock())
    assert session.patient_view(case_id).instructions is None


# --- fix round 1 (I3): the gate matrix - (a) delivered by CASE_RESOLVED, (b) has a
# sub-project 18 source, (c) safety_level LowRisk/MediumRisk, (d) latest entry present --------

def test_instructions_is_null_at_awaiting_patient_input_even_though_an_entry_exists(
        session, sm, app_engine, orchestrator):
    """Gate (a): scenario 1 (§0) loads instructions (step 3) before the missing-document
    escalation (step 4's readiness check) - the entry already exists, but the status is
    `needs_document`, not `completed`, so `instructions` stays null."""
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    assert orchestrator.run_case(case_id) is State.AWAITING_PATIENT_INPUT
    with app_engine.connect() as conn:
        [entry] = data_log.entries(conn, case_id, data_log.DataKind.INSTRUCTIONS)
    assert entry.content is not None  # the entry really is there
    view = session.patient_view(case_id)
    assert view.status == "needs_document"
    assert view.instructions is None


def test_instructions_is_null_for_an_in_review_case(sm, app_engine):
    """Gate (a): `in_review` is never `completed`."""
    d = Driver(sm, app_engine)
    d.submit()
    d.validate("Should I stop taking my blood thinner?")
    d.medical_question()
    assert d.state is State.AWAITING_HUMAN_REVIEW
    session = SessionService(sm)
    view = session.patient_view(d.case_id)
    assert view.status == "in_review"
    assert view.instructions is None


def test_instructions_is_null_for_a_clinical_answer_completion(sm, app_engine):
    """Gate (a): a `clinical_staff` answer to a `MedicalQuestion` completes the case (status
    `completed`) without ever loading instructions - never confused with the agent's own
    CASE_RESOLVED delivery. `instruction_source_id`/an `instructions` entry are poked onto the
    case directly (never producible by a real MedicalQuestion, which never reaches
    CheckAppointment/LoadInstructions at all) precisely so this isolates gate (a) alone -
    gates (b)/(c)/(d) all hold here, and only (a) - delivered by CASE_RESOLVED - does not."""
    from sqlalchemy import update

    from hospital_agent.db import cases
    from hospital_agent.human_review import HumanReviewService

    d = Driver(sm, app_engine)
    d.submit()
    d.validate("Should I stop taking my blood thinner?")
    d.medical_question()
    with app_engine.begin() as conn:
        conn.execute(update(cases).where(cases.c.case_id == d.case_id).values(
            instruction_source_id="INSTR-PREP-COLONOSCOPY", instruction_version="3",
            safety_level="MediumRisk"))
        data_log.record(conn, d.case_id, PATIENT, data_log.DataKind.INSTRUCTIONS,
                        "Colonoscopy prep\nDrink clear liquids only.", sm.clock())
    session = SessionService(sm)
    review = HumanReviewService(sm, session)
    ref = review.context(d.case_id).shown_context_ref
    result = review.answer(reviewer_id="coordinator_nurse", reviewer_role="clinical_staff",
                           case_id=d.case_id, answer="אין להפסיק מדלל דם ללא הנחיית הרופא המטפל.",
                           reason="נענתה בטלפון", shown_context_ref=ref)
    assert result.committed and d.state is State.COMPLETED
    view = session.patient_view(d.case_id)
    assert view.status == "completed" and view.message is not None
    assert view.instructions is None


def test_instructions_is_null_with_no_instruction_source(session, sm, app_engine, orchestrator):
    """Gate (b): a pre-sub-project-18 case shape - an `instructions` entry present, but no
    `instruction_source_id` on the case (poked directly: production's own LoadInstructions
    policy check already fails closed on a missing source, so this shape is defense in depth,
    not a reachable live path)."""
    from sqlalchemy import update

    from hospital_agent.db import cases

    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    assert orchestrator.run_case(case_id) is State.AWAITING_PATIENT_INPUT
    session.upload_document(PATIENT, case_id, "blood_test", "Blood test results: normal.")
    assert orchestrator.run_case(case_id) is State.COMPLETED
    with app_engine.connect() as conn:
        [entry] = data_log.entries(conn, case_id, data_log.DataKind.INSTRUCTIONS)
    assert entry.content is not None
    with app_engine.begin() as conn:
        conn.execute(update(cases).where(cases.c.case_id == case_id).values(instruction_source_id=None))
    assert session.patient_view(case_id).instructions is None


def test_instructions_is_null_when_safety_level_is_high_risk_even_after_a_policy_review_override(
        sm, app_engine):
    """Gate (c): a case whose safety_level was raised to HighRisk by LoadInstructions' own
    Safety re-check, resumed past a PolicyReview escalation by a one-shot human override and
    completed regardless - the delivered status message may still be fine, but the raw
    instructions text is never shown once the case's own safety_level says HighRisk."""
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.retrieve_step(appointment_at=datetime.now(UTC) + timedelta(hours=96),
                    instruction_source_id="INSTR-PREP-COLONOSCOPY", instruction_version="3")
    d.advance()
    d.retrieve_step(required_documents=["referral"], held_documents=["referral"])
    d.advance()
    d.retrieve_step(safety_level="HighRisk")  # LoadInstructions' own Safety re-check
    assert d.case.safety_level == SafetyLevel.HIGH_RISK
    with app_engine.begin() as conn:
        data_log.record(conn, d.case_id, PATIENT, data_log.DataKind.INSTRUCTIONS,
                        "Colonoscopy prep\nDrink clear liquids only.", sm.clock())
    d.assess()  # Z3 readiness: everything held -> Ready
    d.plan_delivery()
    d.propose()
    denied = d.allow()
    assert denied.state_after is State.AWAITING_HUMAN_REVIEW
    assert d.case.escalation_kind is EscalationKind.POLICY_REVIEW
    case = d.case
    d.human(Event.HUMAN_APPROVED, d.approval("approve", plan_hash=case.plan_hash, current_step=case.current_step))
    d.propose()
    assert d.allow().state_after is State.DELIVERING
    assert d.deliver(status="succeeded").committed
    assert d.state is State.COMPLETED
    session = SessionService(sm)
    view = session.patient_view(d.case_id)
    # `message` is null here only because this shortcut never records an OUTGOING_MESSAGE entry
    # for the delivery (`deliver()` emits CASE_RESOLVED directly, bypassing the Tool Executor) -
    # not something this test is about; `status` and `instructions` are.
    assert view.status == "completed"
    assert view.instructions is None


def test_the_history_is_every_status_change_with_its_time(session, sm, app_engine, orchestrator):
    """The patient screen's timeline: what happened, and when (docs/api.md §4)."""
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    assert orchestrator.run_case(case_id) is State.AWAITING_PATIENT_INPUT
    session.upload_document(PATIENT, case_id, "blood_test", "Blood test results: normal.")
    assert orchestrator.run_case(case_id) is State.COMPLETED

    view = session.patient_view(case_id)
    assert [change.status for change in view.history] == [
        "received", "in_progress", "needs_document", "in_progress", "completed"]
    assert view.history[-1].status == view.status
    times = [change.at for change in view.history]
    assert times == sorted(times)


def test_the_history_holds_only_abstract_statuses_and_times(session):
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    view = session.patient_view(case_id)
    assert [change.status for change in view.history] == ["received", "in_progress"]
    assert all(change.status in set(STATUS.values()) for change in view.history)
    assert all(vars(change).keys() == {"status", "at"} for change in view.history)


def test_the_history_skips_blocked_rows_and_unchanged_statuses():
    """Only committed transitions carry a state, and a transition the patient could not have
    noticed - Planning to Executing, both `in_progress` - adds no entry."""
    now = datetime.now(UTC)
    def row(record_type, state_after, seconds):
        return repository.AuditEntry(
            case_id="CASE-X", patient_id=PATIENT, record_type=record_type, event="E",
            state_before=None, state_after=state_after, rule_version="v",
            recorded_at=now.replace(microsecond=seconds))
    trace = [
        row("Transition", State.RECEIVED.value, 1),
        row("Transition", State.CLASSIFYING.value, 2),
        row("Blocked", State.AWAITING_HUMAN_REVIEW.value, 3),  # nothing changed: not a step
        row("Transition", State.PLANNING.value, 4),            # still in_progress
        row("ExecutionStarted", None, 5),
        row("Transition", State.AWAITING_HUMAN_REVIEW.value, 6),
    ]
    history = session_module.status_history(trace, delivered=False)
    assert [(c.status, c.at.microsecond) for c in history] == [
        ("received", 1), ("in_progress", 2), ("in_review", 6)]


def test_a_completed_history_without_a_delivery_reads_closed():
    now = datetime.now(UTC)
    trace = [repository.AuditEntry(
        case_id="CASE-X", patient_id=PATIENT, record_type="Transition", event="E",
        state_before=None, state_after=State.COMPLETED.value, rule_version="v", recorded_at=now)]
    assert [c.status for c in session_module.status_history(trace, delivered=False)] == ["closed"]
    assert [c.status for c in session_module.status_history(trace, delivered=True)] == ["completed"]


def test_a_view_never_shows_a_tombstoned_request_text(session, sm, app_engine):
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    with app_engine.begin() as conn:
        [entry] = data_log.entries(conn, case_id, data_log.DataKind.REQUEST_TEXT)
        data_log.tombstone(conn, entry.entry_id, sm.clock())
    assert session.patient_view(case_id).request_text is None


def test_cases_of_lists_only_this_patients_cases_newest_first(session):
    first = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    session.submit_request("P-20000", REQUEST, identity_verified=True)
    second = session.submit_request(PATIENT, "Which documents do I need?", identity_verified=True)
    assert [view.case_id for view in session.cases_of(PATIENT)] == [second, first]


def test_case_for_patient_hides_another_patients_case(session):
    case_id = session.submit_request(PATIENT, REQUEST, identity_verified=True)
    assert session.case_for_patient(PATIENT, case_id).case_id == case_id
    with pytest.raises(CaseNotFound):
        session.case_for_patient("P-20000", case_id)
    with pytest.raises(CaseNotFound):
        session.case_for_patient(PATIENT, "CASE-DOES-NOT-EXIST")


# --- uploads -----------------------------------------------------------------------------------

def _awaiting_document(sm, app_engine) -> Driver:
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"])
    d.missing_information(z3_result="unsat")
    assert d.state is State.AWAITING_PATIENT_INPUT
    return d


def test_an_upload_to_another_patients_case_is_not_found(session, sm, app_engine):
    d = _awaiting_document(sm, app_engine)
    with pytest.raises(CaseNotFound):
        session.upload_document("P-20000", d.case_id, "blood_test", "results")
    assert uploads(app_engine, d.case_id) == []


def test_d25_a_rejected_upload_is_tombstoned_and_changes_nothing(session, sm, app_engine, wake):
    d = _awaiting_document(sm, app_engine)
    version = d.case.state_version
    result = session.upload_document(PATIENT, d.case_id, "blood_test", "someone else's results",
                                     document_extra={"patient_id": "P-OTHER"})
    assert result.state_after is State.AWAITING_PATIENT_INPUT
    assert d.case.held_documents == ["referral"]
    [entry] = uploads(app_engine, d.case_id)
    assert entry.content is None and entry.deleted_at is not None
    assert wake.calls == 1
    assert d.case.state_version == version + 1  # the §3 "DocumentValid does not hold" self-loop
    with app_engine.connect() as conn:
        row = [r for r in repository.load_trace(conn, d.case_id) if r.event == "DOCUMENT_UPLOADED"][-1]
    assert (row.record_type, row.state_before, row.state_after) == (
        "Transition", State.AWAITING_PATIENT_INPUT.value, State.AWAITING_PATIENT_INPUT.value)
    assert row.guards == {"!DocumentValid": True}  # rejected by the guard, not silently dropped
    assert row.content_hash == entry.content_hash


def test_an_accepted_upload_is_kept_and_its_hash_is_on_the_audit_row(session, sm, app_engine):
    d = _awaiting_document(sm, app_engine)
    result = session.upload_document(PATIENT, d.case_id, "blood_test", "Blood test results: normal.")
    assert result.committed and result.state_after is State.CLASSIFYING
    [entry] = uploads(app_engine, d.case_id)
    assert entry.content == "Blood test results: normal."
    with app_engine.connect() as conn:
        row = [r for r in repository.load_trace(conn, d.case_id) if r.event == "DOCUMENT_UPLOADED"][-1]
    assert (row.record_type, row.content_hash) == ("Transition", entry.content_hash)
