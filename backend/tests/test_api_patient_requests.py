"""Sub-project 15's routes (design §10)."""
import pytest
from fastapi.testclient import TestClient

from hospital_agent import repository
from hospital_agent.api.app import create_app
from hospital_agent.auth import demo_password
from tests.test_patient_request_fsm import escalated
from tests.test_pdf_upload import PDF, FakeIntake, accepted

NURSE, ADMIN, PATIENT, OTHER = "coordinator_nurse", "admin_coordinator", "P-10041", "P-20000"


@pytest.fixture
def intake():
    return FakeIntake(accepted("URINALYSIS"))


@pytest.fixture
def client(app_engine, intake):
    with TestClient(create_app(app_engine, document_intake=intake)) as test_client:
        yield test_client


def auth(client, user_id):
    token = client.post("/api/auth/login", json={"user_id": user_id, "password": demo_password()}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def context_ref(client, case_id, headers):
    return client.get(f"/api/staff/cases/{case_id}/context", headers=headers).json()["shown_context_ref"]


def test_templates_are_served_to_staff_only(client):
    assert client.get("/api/staff/message-templates", headers=auth(client, PATIENT)).status_code == 403
    body = client.get("/api/staff/message-templates", headers=auth(client, ADMIN)).json()
    assert {"template_id", "purpose", "text", "param", "options"} == set(body[0])


def test_a_question_round_trip(client, sm, app_engine):
    d = escalated(sm, app_engine)
    staff = auth(client, ADMIN)
    response = client.post(f"/api/staff/cases/{d.case_id}/request", headers=staff, json={
        "kind": "question", "template_id": "clarify_general", "reason": "unclear",
        "shown_context_ref": context_ref(client, d.case_id, staff)})
    assert response.status_code == 200 and response.json()["state"] == "AwaitingPatientReply"
    patient = auth(client, PATIENT)
    view = client.get(f"/api/patient/requests/{d.case_id}", headers=patient).json()
    assert view["status"] == "needs_reply" and view["reply_request"]["kind"] == "question"
    replied = client.post(f"/api/patient/requests/{d.case_id}/reply", headers=patient, json={"text": "תור לאורתופדיה"})
    assert replied.status_code == 200 and replied.json()["status"] == "in_review"
    queue = client.get("/api/staff/reviews", headers=staff).json()["items"]
    item = next(i for i in queue if i["case_id"] == d.case_id)
    assert item["returned_by"] == "patient_reply" and "approve" not in item["allowed_decisions"]


def test_free_text_from_admin_staff_is_403(client, sm, app_engine):
    d = escalated(sm, app_engine)
    staff = auth(client, ADMIN)
    response = client.post(f"/api/staff/cases/{d.case_id}/request", headers=staff, json={
        "kind": "question", "text": "נא לפרט", "reason": "unclear",
        "shown_context_ref": context_ref(client, d.case_id, staff)})
    assert (response.status_code, response.json()["detail"]) == (403, "clinical_staff_only")


def test_a_document_round_trip_through_the_file_route(client, sm, app_engine):
    d = escalated(sm, app_engine)
    staff = auth(client, NURSE)
    client.post(f"/api/staff/cases/{d.case_id}/request", headers=staff, json={
        "kind": "document", "document_type": "URINALYSIS", "reason": "need urine test",
        "shown_context_ref": context_ref(client, d.case_id, staff)})
    patient = auth(client, PATIENT)
    assert client.post(f"/api/patient/requests/{d.case_id}/reply", headers=patient,
                       json={"text": "hi"}).json()["detail"] == "reply_kind_mismatch"
    uploaded = client.post(f"/api/patient/requests/{d.case_id}/reply/file", headers=patient,
                           files={"file": ("urine.pdf", PDF, "application/pdf")})
    assert uploaded.status_code == 200
    assert uploaded.json()["upload"] == {"code": "accepted", "document_type": "URINALYSIS"}
    assert uploaded.json()["request"]["status"] == "in_review"


def test_a_closing_message_through_the_decision_route(client, sm, app_engine):
    d = escalated(sm, app_engine)
    staff = auth(client, ADMIN)
    response = client.post(f"/api/staff/cases/{d.case_id}/decision", headers=staff, json={
        "decision": "reject", "reason": "out of scope", "shown_context_ref": context_ref(client, d.case_id, staff),
        "message": {"template_id": "close_out_of_scope"}})
    assert response.status_code == 200
    view = client.get(f"/api/patient/requests/{d.case_id}", headers=auth(client, PATIENT)).json()
    assert view["status"] == "closed" and view["message"].startswith("פנייתך אינה בתחום")


def test_replying_when_nothing_was_asked_is_409(client, sm, app_engine):
    d = escalated(sm, app_engine)
    response = client.post(f"/api/patient/requests/{d.case_id}/reply", headers=auth(client, PATIENT),
                           json={"text": "hi"})
    assert (response.status_code, response.json()["detail"]) == (409, "not_waiting_for_reply")


def test_a_whitespace_only_reply_is_422(client, sm, app_engine):
    d = escalated(sm, app_engine)
    response = client.post(f"/api/patient/requests/{d.case_id}/reply", headers=auth(client, PATIENT),
                           json={"text": "   "})
    assert (response.status_code, response.json()["detail"]) == (422, "invalid_body")


def test_a_document_reply_of_the_wrong_type_is_reported_not_accepted(sm, app_engine):
    intake = FakeIntake(accepted("CBC"))
    with TestClient(create_app(app_engine, document_intake=intake)) as client:
        d = escalated(sm, app_engine)
        staff = auth(client, NURSE)
        client.post(f"/api/staff/cases/{d.case_id}/request", headers=staff, json={
            "kind": "document", "document_type": "URINALYSIS", "reason": "need urine test",
            "shown_context_ref": context_ref(client, d.case_id, staff)})
        uploaded = client.post(f"/api/patient/requests/{d.case_id}/reply/file", headers=auth(client, PATIENT),
                               files={"file": ("blood.pdf", PDF, "application/pdf")})
        assert uploaded.status_code == 200
        assert uploaded.json()["upload"] == {"code": "wrong_document_type", "document_type": "CBC"}
        assert uploaded.json()["request"]["status"] == "needs_reply"  # unchanged


def test_free_text_on_a_decision_from_admin_staff_is_403(client, sm, app_engine):
    d = escalated(sm, app_engine)
    staff = auth(client, ADMIN)
    response = client.post(f"/api/staff/cases/{d.case_id}/decision", headers=staff, json={
        "decision": "resolve", "reason": "handled", "shown_context_ref": context_ref(client, d.case_id, staff),
        "message": {"text": "פנייתך טופלה"}})
    assert (response.status_code, response.json()["detail"]) == (403, "clinical_staff_only")


def test_a_message_on_approve_is_409_message_not_allowed(client, sm, app_engine):
    d = escalated(sm, app_engine)  # RetryExhausted: approve is otherwise legal here
    staff = auth(client, ADMIN)
    response = client.post(f"/api/staff/cases/{d.case_id}/decision", headers=staff, json={
        "decision": "approve", "reason": "ok", "shown_context_ref": context_ref(client, d.case_id, staff),
        "message": {"template_id": "close_out_of_scope"}})
    assert (response.status_code, response.json()["detail"]) == (409, "message_not_allowed")


def test_a_request_after_the_appointment_has_passed_is_409(client, sm, app_engine):
    from sqlalchemy import text
    d = escalated(sm, app_engine)
    with app_engine.begin() as conn:
        conn.execute(text("UPDATE cases SET appointment_at = now() - interval '1 minute' WHERE case_id = :c"),
                     {"c": d.case_id})
    staff = auth(client, ADMIN)
    response = client.post(f"/api/staff/cases/{d.case_id}/request", headers=staff, json={
        "kind": "question", "template_id": "clarify_general", "reason": "unclear",
        "shown_context_ref": context_ref(client, d.case_id, staff)})
    assert (response.status_code, response.json()["detail"]) == (409, "appointment_passed")


def test_approve_is_409_human_engaged_once_a_request_was_sent(client, sm, app_engine):
    d = escalated(sm, app_engine)
    staff = auth(client, ADMIN)
    client.post(f"/api/staff/cases/{d.case_id}/request", headers=staff, json={
        "kind": "question", "template_id": "clarify_general", "reason": "unclear",
        "shown_context_ref": context_ref(client, d.case_id, staff)})
    patient = auth(client, PATIENT)
    client.post(f"/api/patient/requests/{d.case_id}/reply", headers=patient, json={"text": "תור לאורתופדיה"})
    response = client.post(f"/api/staff/cases/{d.case_id}/decision", headers=staff, json={
        "decision": "approve", "reason": "ok", "shown_context_ref": context_ref(client, d.case_id, staff)})
    assert (response.status_code, response.json()["detail"]) == (409, "human_engaged")


def test_another_patients_case_is_404_on_both_reply_routes(client, sm, app_engine):
    d = escalated(sm, app_engine)
    other = auth(client, OTHER)
    text_reply = client.post(f"/api/patient/requests/{d.case_id}/reply", headers=other, json={"text": "hi"})
    assert (text_reply.status_code, text_reply.json()["detail"]) == (404, "case_not_found")
    file_reply = client.post(f"/api/patient/requests/{d.case_id}/reply/file", headers=other,
                             files={"file": ("x.pdf", PDF, "application/pdf")})
    assert (file_reply.status_code, file_reply.json()["detail"]) == (404, "case_not_found")


def test_a_staff_token_is_forbidden_on_both_reply_routes(client, sm, app_engine):
    d = escalated(sm, app_engine)
    staff = auth(client, NURSE)
    text_reply = client.post(f"/api/patient/requests/{d.case_id}/reply", headers=staff, json={"text": "hi"})
    assert (text_reply.status_code, text_reply.json()["detail"]) == (403, "patients_only")
    file_reply = client.post(f"/api/patient/requests/{d.case_id}/reply/file", headers=staff,
                             files={"file": ("x.pdf", PDF, "application/pdf")})
    assert (file_reply.status_code, file_reply.json()["detail"]) == (403, "patients_only")


def test_the_reviewer_identity_on_a_request_comes_from_the_token_only(client, sm, app_engine):
    d = escalated(sm, app_engine)
    staff = auth(client, NURSE)
    body = {"kind": "question", "template_id": "clarify_general", "reason": "unclear",
            "shown_context_ref": context_ref(client, d.case_id, staff),
            "reviewer_id": "P-10041", "reviewer_role": "patient"}  # ignored: §18.3
    assert client.post(f"/api/staff/cases/{d.case_id}/request", headers=staff, json=body).status_code == 200
    with app_engine.connect() as conn:
        approval_ids = [r.approval_id for r in repository.load_trace(conn, d.case_id) if r.approval_id]
        approval = repository.load_approval(conn, approval_ids[-1])
    assert (approval.reviewer_id, approval.reviewer_role) == (NURSE, "clinical_staff")
