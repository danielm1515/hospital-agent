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
    classified = Driver(sm, app_engine)
    classified.to_classified()
    received = Driver(sm, app_engine, patient_id="P-20000")
    received.submit()

    all_cases = client.get("/api/staff/cases", headers=staff).json()
    assert {c["case_id"] for c in all_cases} == {classified.case_id, received.case_id}
    assert set(all_cases[0]) == {"case_id", "state", "escalation_kind", "updated_at"}

    only_received = client.get("/api/staff/cases", params={"state": "Received"}, headers=staff).json()
    assert [c["case_id"] for c in only_received] == [received.case_id]

    assert client.get("/api/staff/cases", params={"state": "Sleeping"}, headers=staff).status_code == 422


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
                             "allowed_decisions", "required_fields", "updated_at"}
    assert queue[0]["escalation_kind"] == "MedicalQuestion"
    assert queue[0]["allowed_decisions"] == ["resolve", "reject"]
    assert queue[1]["allowed_decisions"] == ["approve", "resolve", "reject"]
    assert queue[1]["required_fields"] == []


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
