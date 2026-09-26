"""The staff side of the authenticated API: the Case Monitor behind staff auth, the review
queue, the shown context, the decision and a Data Log tombstone (design §5, §6).

The reviewer's identity comes from the token only (§18.3): the body carries no reviewer_id.
"""
import pytest
from fastapi.testclient import TestClient

from hospital_agent import data_log, repository
from hospital_agent.api.app import create_app
from hospital_agent.auth import demo_password
from hospital_agent.naming import EscalationKind, State
from tests.driver import Driver

NURSE, ADMIN, PATIENT = "coordinator_nurse", "admin_coordinator", "P-10041"
MEDICAL = "Should I stop taking my blood thinner?"


@pytest.fixture
def client(app_engine):
    with TestClient(create_app(app_engine)) as test_client:
        yield test_client


@pytest.fixture
def staff(client):
    token = client.post("/api/auth/login", json={"user_id": NURSE, "password": demo_password()}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def medical_question(sm, app_engine) -> Driver:
    d = Driver(sm, app_engine)
    d.submit()
    d.validate(MEDICAL)
    d.medical_question()
    assert d.case.escalation_kind is EscalationKind.MEDICAL_QUESTION
    return d


def retry_exhausted(sm, app_engine) -> Driver:
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    d.retry_exhausted()
    return d


def decide(client, staff, case_id, decision, **fields):
    ref = client.get(f"/api/staff/cases/{case_id}/context", headers=staff).json()["shown_context_ref"]
    body = {"decision": decision, "reason": "reviewed by staff", "shown_context_ref": ref, **fields}
    return client.post(f"/api/staff/cases/{case_id}/decision", json=body, headers=staff)


# --- the Case Monitor, now behind staff auth ----------------------------------------------

def test_the_old_public_case_routes_are_gone(client, sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    for path in ("/cases", f"/cases/{d.case_id}", f"/cases/{d.case_id}/audit"):
        assert client.get(path).status_code == 404


def test_the_monitor_needs_a_staff_token(client, sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    assert client.get("/api/staff/cases").status_code == 401
    assert client.get(f"/api/staff/cases/{d.case_id}").status_code == 401


def test_cases_list_and_filter_by_state(client, staff, sm, app_engine):
    """Staff-fixes design Task 3: `{items, next_cursor}`, every column the table shows."""
    classified = Driver(sm, app_engine)
    classified.to_classified()
    received = Driver(sm, app_engine, patient_id="P-20000")
    received.submit()

    page = client.get("/api/staff/cases", headers=staff).json()
    assert {c["case_id"] for c in page["items"]} == {classified.case_id, received.case_id}
    assert set(page["items"][0]) == {"case_id", "patient_id", "state", "intent", "safety_level",
                                     "escalation_kind", "escalated_from_state", "created_at", "updated_at"}
    assert page["next_cursor"] is None

    only_received = client.get("/api/staff/cases", params={"state": "Received"}, headers=staff).json()
    assert [c["case_id"] for c in only_received["items"]] == [received.case_id]
    assert only_received["items"][0]["patient_id"] == "P-20000"

    assert client.get("/api/staff/cases", params={"state": "Sleeping"}, headers=staff).status_code == 422


def test_cases_cursor_round_trips():
    from datetime import UTC, datetime

    at = datetime(2026, 9, 19, 22, 12, 48, 986200, tzinfo=UTC)
    cursor = repository.encode_cases_cursor(at, "CASE-23FE645294B7")
    assert repository.decode_cases_cursor(cursor) == (at, "CASE-23FE645294B7")


def test_cases_list_is_paginated_with_a_keyset_cursor(client, staff, sm, app_engine):
    cases = [Driver(sm, app_engine, patient_id=f"P-{30000 + i}") for i in range(3)]
    for d in cases:
        d.submit()
    ordered = sorted(cases, key=lambda d: (d.case.updated_at, d.case_id), reverse=True)

    first = client.get("/api/staff/cases", params={"limit": 2}, headers=staff).json()
    assert [c["case_id"] for c in first["items"]] == [d.case_id for d in ordered[:2]]
    assert first["next_cursor"] is not None

    second = client.get("/api/staff/cases", params={"limit": 2, "cursor": first["next_cursor"]},
                        headers=staff).json()
    assert [c["case_id"] for c in second["items"]] == [d.case_id for d in ordered[2:]]
    assert second["next_cursor"] is None


@pytest.mark.parametrize("limit", [0, 201, -1])
def test_cases_list_rejects_an_invalid_limit(client, staff, limit):
    response = client.get("/api/staff/cases", params={"limit": limit}, headers=staff)
    assert response.status_code == 422 and response.json() == {"detail": "invalid_limit"}


def awaiting_patient_reply(sm, app_engine) -> Driver:
    """A case a staff member asked the patient a question of (sub-project 15): AwaitingPatientReply."""
    from datetime import UTC, datetime, timedelta

    from hospital_agent.naming import Component, Event

    d = retry_exhausted(sm, app_engine)
    approval_id = d.approval("request", patient_deadline=datetime.now(UTC) + timedelta(hours=24))
    payload = {"approval_id": approval_id, "reply_kind": "question", "requested_document": None,
               "content_hash": "HASH-MESSAGE"}
    result = sm.apply(d.case_id, Event.PATIENT_REPLY_REQUESTED, payload, Component.EXTERNAL)
    assert result.committed and result.state_after is State.AWAITING_PATIENT_REPLY
    return d


def test_cases_list_filters_by_group(client, staff, sm, app_engine):
    """Staff-fixes design Task 4: `?group=` widens the filter to a whole STATE_GROUPS set."""
    medical = medical_question(sm, app_engine)
    reply = awaiting_patient_reply(sm, app_engine)
    automatic = Driver(sm, app_engine, patient_id="P-20002")
    automatic.to_classified()

    staff_group = client.get("/api/staff/cases", params={"group": "staff"}, headers=staff).json()
    assert {c["case_id"] for c in staff_group["items"]} == {medical.case_id}

    patient_group = client.get("/api/staff/cases", params={"group": "patient"}, headers=staff).json()
    assert {c["case_id"] for c in patient_group["items"]} == {reply.case_id}

    automatic_group = client.get("/api/staff/cases", params={"group": "automatic"}, headers=staff).json()
    assert {c["case_id"] for c in automatic_group["items"]} == {automatic.case_id}

    assert client.get("/api/staff/cases", params={"group": "not-a-group"}, headers=staff).status_code == 422


def test_cases_list_group_staff_accepts_an_escalation_kind_filter(client, staff, sm, app_engine):
    medical = medical_question(sm, app_engine)
    retry = retry_exhausted(sm, app_engine)

    only_medical = client.get("/api/staff/cases", params={"group": "staff", "escalation_kind": "MedicalQuestion"},
                              headers=staff).json()
    assert [c["case_id"] for c in only_medical["items"]] == [medical.case_id]

    only_retry = client.get("/api/staff/cases", params={"group": "staff", "escalation_kind": "RetryExhausted"},
                            headers=staff).json()
    assert [c["case_id"] for c in only_retry["items"]] == [retry.case_id]

    assert client.get("/api/staff/cases", params={"group": "staff", "escalation_kind": "NotAKind"},
                      headers=staff).status_code == 422


def test_cases_list_rejects_an_escalation_kind_filter_outside_group_staff(client, staff, sm, app_engine):
    medical_question(sm, app_engine)

    without_group = client.get("/api/staff/cases", params={"escalation_kind": "MedicalQuestion"}, headers=staff)
    assert without_group.status_code == 422 and without_group.json() == {"detail": "invalid_filter"}

    other_group = client.get("/api/staff/cases", params={"group": "done", "escalation_kind": "MedicalQuestion"},
                             headers=staff)
    assert other_group.status_code == 422 and other_group.json() == {"detail": "invalid_filter"}


def test_cases_list_runs_one_sql_statement_regardless_of_row_count(client, staff, sm, app_engine):
    """Staff-fixes design Task 3: before, the Case Monitor's N+1 was in the browser (one
    `getCase` per row); the list route itself was always one `SELECT`. This pins that it
    stays exactly one statement as the row count grows, so a future change cannot
    reintroduce a per-row query here either."""
    from sqlalchemy import event

    for i in range(5):
        Driver(sm, app_engine, patient_id=f"P-{40000 + i}").submit()

    statements = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(app_engine, "before_cursor_execute", _count)
    try:
        response = client.get("/api/staff/cases", headers=staff)
    finally:
        event.remove(app_engine, "before_cursor_execute", _count)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 5
    assert len(statements) == 1


def test_cases_list_rejects_a_bad_cursor(client, staff):
    # Valid base64, but not `updated_at|case_id` - decode_cases_cursor must still refuse it.
    response = client.get("/api/staff/cases", params={"cursor": "bm9uc2Vuc2U="}, headers=staff)
    assert response.status_code == 422 and response.json() == {"detail": "invalid_cursor"}


def test_case_detail_and_404(client, staff, sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    detail = client.get(f"/api/staff/cases/{d.case_id}", headers=staff).json()
    assert (detail["state"], detail["state_version"], detail["identity_verified"]) == ("Classified", 3, True)
    assert detail["safety_level"] == "MediumRisk"
    missing = client.get("/api/staff/cases/CASE-NOPE", headers=staff)
    assert missing.status_code == 404 and missing.json() == {"detail": "case_not_found"}


def test_case_audit_is_the_ordered_trace(client, staff, sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    audit = client.get(f"/api/staff/cases/{d.case_id}/audit", headers=staff).json()
    assert [row["event"] for row in audit] == ["REQUEST_SUBMITTED", "REQUEST_VALIDATED", "INTENT_CLASSIFIED"]
    assert [row["audit_id"] for row in audit] == sorted(row["audit_id"] for row in audit)
    assert client.get("/api/staff/cases/CASE-NOPE/audit", headers=staff).status_code == 404


# --- the review queue ------------------------------------------------------------------------

def test_the_review_queue_shape(client, staff, sm, app_engine):
    medical = medical_question(sm, app_engine)
    retry = retry_exhausted(sm, app_engine)
    Driver(sm, app_engine).to_classified()  # not in review

    queue = client.get("/api/staff/reviews", headers=staff).json()
    assert [item["case_id"] for item in queue] == [medical.case_id, retry.case_id]
    assert set(queue[0]) == {"case_id", "patient_id", "escalation_kind", "escalated_from_state", "reasons",
                             "allowed_decisions", "required_fields", "updated_at", "human_engaged", "returned_by"}
    assert queue[0]["escalation_kind"] == "MedicalQuestion"
    assert queue[0]["allowed_decisions"] == ["resolve", "reject"]
    assert queue[1]["allowed_decisions"] == ["approve", "resolve", "reject"]
    assert queue[1]["required_fields"] == []


def test_one_review_item_by_case_id(client, staff, sm, app_engine):
    """Staff-fixes design Task 3: `ReviewCase` reads its own row without the whole queue."""
    medical = medical_question(sm, app_engine)
    not_escalated = Driver(sm, app_engine)
    not_escalated.to_classified()

    item = client.get(f"/api/staff/reviews/{medical.case_id}", headers=staff).json()
    assert item["case_id"] == medical.case_id
    assert set(item) == {"case_id", "patient_id", "escalation_kind", "escalated_from_state", "reasons",
                         "allowed_decisions", "required_fields", "updated_at", "human_engaged", "returned_by"}

    refused = client.get(f"/api/staff/reviews/{not_escalated.case_id}", headers=staff)
    assert refused.status_code == 404 and refused.json() == {"detail": "not_in_review"}

    missing = client.get("/api/staff/reviews/CASE-NOPE", headers=staff)
    assert missing.status_code == 404 and missing.json() == {"detail": "case_not_found"}


def test_the_context_shows_the_data_log_and_the_trace(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)
    context = client.get(f"/api/staff/cases/{d.case_id}/context", headers=staff).json()
    assert set(context) == {"case_id", "patient_id", "state", "escalation_kind", "escalated_from_state",
                            "reasons", "data", "trace", "shown_context_ref"}
    assert context["state"] == "AwaitingHumanReview"
    assert [entry["kind"] for entry in context["data"]] == ["request_text"]
    assert context["data"][0]["content"] == MEDICAL
    assert [row["event"] for row in context["trace"]][:2] == ["REQUEST_SUBMITTED", "REQUEST_VALIDATED"]
    assert context["shown_context_ref"].startswith("ctx-")
    assert client.get("/api/staff/cases/CASE-NOPE/context", headers=staff).status_code == 404


# --- deciding --------------------------------------------------------------------------------

def test_resolving_a_medical_question_round_trip(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)
    response = decide(client, staff, d.case_id, "resolve")
    assert response.status_code == 200
    assert response.json() == {"case_id": d.case_id, "state": "Completed"}
    assert d.state is State.COMPLETED
    assert client.get("/api/staff/reviews", headers=staff).json() == []


def test_approving_retry_exhausted_opens_a_new_cycle(client, staff, sm, app_engine):
    d = retry_exhausted(sm, app_engine)
    assert decide(client, staff, d.case_id, "approve").json()["state"] == "Planning"
    assert (d.case.retry_cycle, d.case.attempt_count) == (1, 0)


def test_approving_identity_without_a_request_text_keeps_the_case_in_received(client, staff, app_engine):
    """The decision is already committed when the re-validation cannot run, so it is never
    reported as an error: 200, and the case waits for the staff in Received."""
    token = client.post("/api/auth/login", json={"user_id": "P-30000", "password": demo_password()}).json()["token"]
    case_id = client.post("/api/patient/requests", json={"text": MEDICAL.replace("?", ".")},
                          headers={"Authorization": f"Bearer {token}"}).json()["case_id"]
    with app_engine.connect() as conn:
        [entry] = data_log.entries(conn, case_id, data_log.DataKind.REQUEST_TEXT)
    assert client.delete(f"/api/staff/cases/{case_id}/data/{entry.entry_id}", headers=staff).status_code == 204

    response = decide(client, staff, case_id, "approve", verified_identity_ref="ID-DESK-17")
    assert response.status_code == 200
    assert response.json() == {"case_id": case_id, "state": "Received"}


def test_a_stale_shown_context_ref_is_409(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)
    response = decide(client, staff, d.case_id, "resolve", shown_context_ref="ctx-stale")
    assert response.status_code == 409 and response.json() == {"detail": "context_changed"}
    assert d.state is State.AWAITING_HUMAN_REVIEW


def test_approving_a_medical_question_is_409(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)
    response = decide(client, staff, d.case_id, "approve")
    assert response.status_code == 409 and response.json() == {"detail": "decision_not_allowed"}
    assert d.state is State.AWAITING_HUMAN_REVIEW


@pytest.mark.parametrize(("decision", "reason", "code"), [
    ("escalate", "reviewed", "invalid_decision"),
    ("resolve", "  ", "reason_required"),
])
def test_invalid_decision_input_is_409(client, staff, sm, app_engine, decision, reason, code):
    d = medical_question(sm, app_engine)
    response = decide(client, staff, d.case_id, decision, reason=reason)
    assert response.status_code == 409 and response.json() == {"detail": code}


def test_a_decision_on_a_case_not_in_review_is_409(client, staff, sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    response = decide(client, staff, d.case_id, "resolve")
    assert response.status_code == 409 and response.json() == {"detail": "not_in_review"}


def test_a_decision_on_an_unknown_case_is_404(client, staff):
    response = client.post("/api/staff/cases/CASE-NOPE/decision", headers=staff,
                           json={"decision": "resolve", "reason": "x", "shown_context_ref": "ctx-x"})
    assert response.status_code == 404 and response.json() == {"detail": "case_not_found"}


def test_the_reviewer_identity_comes_from_the_token_only(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)
    ref = client.get(f"/api/staff/cases/{d.case_id}/context", headers=staff).json()["shown_context_ref"]
    body = {"decision": "resolve", "reason": "reviewed", "shown_context_ref": ref,
            "reviewer_id": "P-10041", "reviewer_role": "patient"}  # ignored: §18.3
    assert client.post(f"/api/staff/cases/{d.case_id}/decision", json=body, headers=staff).status_code == 200
    with app_engine.connect() as conn:
        approval_ids = [r.approval_id for r in repository.load_trace(conn, d.case_id) if r.approval_id]
        approval = repository.load_approval(conn, approval_ids[-1])
    assert (approval.reviewer_id, approval.reviewer_role) == (NURSE, "clinical_staff")


def test_admin_staff_may_decide_too(client, sm, app_engine):
    token = client.post("/api/auth/login", json={"user_id": ADMIN, "password": demo_password()}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    d = medical_question(sm, app_engine)
    assert decide(client, headers, d.case_id, "reject").json()["state"] == "Failed"


# --- tombstone (§18.4) -------------------------------------------------------------------------

def test_tombstone_clears_the_entry_once(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)
    with app_engine.connect() as conn:
        [entry] = data_log.entries(conn, d.case_id, data_log.DataKind.REQUEST_TEXT)
    path = f"/api/staff/cases/{d.case_id}/data/{entry.entry_id}"
    assert client.delete(path, headers=staff).status_code == 204
    again = client.delete(path, headers=staff)
    assert again.status_code == 404 and again.json() == {"detail": "entry_not_found"}
    assert client.delete(f"/api/staff/cases/{d.case_id}/data/DATA-NOPE", headers=staff).status_code == 404
    context = client.get(f"/api/staff/cases/{d.case_id}/context", headers=staff).json()
    assert context["data"] == []


# --- the clinical answer (§5 AnswerClinicalQuestion, §12.4) ----------------------------------

def token_for(client, user_id):
    token = client.post("/api/auth/login", json={"user_id": user_id, "password": demo_password()}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def answer(client, headers, case_id, **overrides):
    ref = client.get(f"/api/staff/cases/{case_id}/context", headers=headers).json()["shown_context_ref"]
    body = {"answer": "אין להפסיק את הטיפול ללא הנחיית הרופא.", "reason": "נענתה טלפונית",
            "shown_context_ref": ref, **overrides}
    return client.post(f"/api/staff/cases/{case_id}/answer", json=body, headers=headers)


def test_a_nurse_answers_a_medical_question_through_the_api(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)

    answered = answer(client, staff, d.case_id)

    assert answered.status_code == 200
    assert answered.json() == {"case_id": d.case_id, "state": "Completed"}


def test_admin_staff_gets_403_from_the_answer_route(client, sm, app_engine):
    """§12.4: a ContentApproval is clinical_staff only. admin_staff may still resolve or reject."""
    d = medical_question(sm, app_engine)

    refused = answer(client, token_for(client, ADMIN), d.case_id)

    assert refused.status_code == 403 and refused.json()["detail"] == "clinical_staff_only"
    assert sm.load(d.case_id).state is State.AWAITING_HUMAN_REVIEW
    with app_engine.connect() as conn:
        assert data_log.entries(conn, d.case_id, data_log.DataKind.OUTGOING_MESSAGE) == []
        assert repository.content_approvals_for(conn, d.case_id, None) == []


def test_an_escalation_that_is_not_a_medical_question_is_409(client, staff, sm, app_engine):
    d = retry_exhausted(sm, app_engine)

    refused = answer(client, staff, d.case_id)

    assert refused.status_code == 409 and refused.json()["detail"] == "decision_not_allowed"


def test_a_whitespace_only_answer_is_409_answer_required(client, staff, sm, app_engine):
    """The schema's min_length=1 lets a whitespace-only string through; the service's own
    trim check is what actually rejects it - so this input path needs its own coverage."""
    d = medical_question(sm, app_engine)

    refused = answer(client, staff, d.case_id, answer="   ")

    assert refused.status_code == 409 and refused.json()["detail"] == "answer_required"


def test_a_whitespace_only_reason_is_409_reason_required(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)

    refused = answer(client, staff, d.case_id, reason="   ")

    assert refused.status_code == 409 and refused.json()["detail"] == "reason_required"


def test_the_answer_route_refuses_an_empty_body_without_echoing_it(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)

    refused = client.post(f"/api/staff/cases/{d.case_id}/answer",
                          json={"answer": "", "reason": "", "shown_context_ref": ""}, headers=staff)

    assert refused.status_code == 422 and refused.json() == {"detail": "invalid_body"}


def test_a_stale_context_ref_is_409_context_changed(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)

    refused = answer(client, staff, d.case_id, shown_context_ref="ctx-stale")

    assert refused.status_code == 409 and refused.json()["detail"] == "context_changed"


def test_a_patient_token_cannot_reach_the_answer_route(client, sm, app_engine):
    d = medical_question(sm, app_engine)

    refused = client.post(f"/api/staff/cases/{d.case_id}/answer",
                          json={"answer": "תשובה", "reason": "סיבה", "shown_context_ref": "ctx"},
                          headers=token_for(client, PATIENT))

    assert refused.status_code == 403


# --- staff-fixes design Task 1: GET /api/staff/system-status --------------------------------

def test_system_status_is_staff_only(client, sm, app_engine):
    refused = client.get("/api/staff/system-status", headers=token_for(client, PATIENT))
    assert refused.status_code == 403 and refused.json()["detail"] == "staff_only"


def test_system_status_shape_on_an_injected_test_server(client, staff):
    """`client` here is built on an injected engine, exactly like every other test in this
    file - no Orchestrator runs, so `orchestrator` is null and the LLM never ran."""
    response = client.get("/api/staff/system-status", headers=staff)
    assert response.status_code == 200
    assert response.json() == {"orchestrator": None,
                               "llm": {"last_ok_at": None, "last_error": None, "last_error_at": None}}


def test_system_status_reports_the_llm_telemetry(client, staff):
    from hospital_agent.llm import telemetry
    from hospital_agent.llm.schemas import Call

    telemetry.record(Call.INTENT, "gpt-5.6-luna", 12, "api:RateLimitError:insufficient_quota")

    response = client.get("/api/staff/system-status", headers=staff)

    body = response.json()
    assert body["llm"]["last_error"] == "api:RateLimitError:insufficient_quota"
    assert body["llm"]["last_error_at"] is not None
    assert body["llm"]["last_ok_at"] is None
