"""The Session Service (spec §1, §12.3, D24, D25; sub-project 5 design §4)."""
from datetime import UTC, datetime

import pytest

from hospital_agent import data_log, repository
from hospital_agent import session as session_module
from hospital_agent.case import CaseRecord
from hospital_agent.execution.gateway import MockGateway
from hospital_agent.llm.orchestrator import Orchestrator
from hospital_agent.llm.provider import FakeProvider
from hospital_agent.naming import Component, EscalationKind, Event, State
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
