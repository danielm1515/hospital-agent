"""The patient side of the authenticated API: login, identity and the patient routes
(spec §1, §18.3; sub-project 5 design §3, §6).

Identity always comes from the token: a patient only ever sees their own cases, and the
request body can never name a patient_id (§18.3).
"""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from hospital_agent import repository
from hospital_agent.api.app import create_app
from hospital_agent.auth import User, auth_secret, demo_password, issue_token

REQUEST = "When is my appointment and which documents do I need?"
PATIENT, OTHER, NURSE = "P-10041", "P-20000", "coordinator_nurse"
# Token subjects only: issue_token() reads no password, and the API re-reads the user from the table.
PATIENT_USER = User(PATIENT, "patient", "דנה כהן", password_hash="-")
NURSE_USER = User(NURSE, "clinical_staff", "אחות מתאמת", password_hash="-")


@pytest.fixture
def client(app_engine):
    with TestClient(create_app(app_engine)) as test_client:
        yield test_client


def login(client, user_id, password=None):
    return client.post("/api/auth/login", json={"user_id": user_id, "password": password or demo_password()})


def auth(client, user_id):
    return {"Authorization": f"Bearer {login(client, user_id).json()['token']}"}


def submit(client, user_id, text=REQUEST):
    return client.post("/api/patient/requests", json={"text": text}, headers=auth(client, user_id))


# --- login and identity --------------------------------------------------------------------

def test_login_returns_a_token_and_the_identity(client):
    response = login(client, PATIENT)
    assert response.status_code == 200
    body = response.json()
    assert (body["user_id"], body["role"]) == (PATIENT, "patient")
    assert body["display_name"] == "דנה כהן"
    assert body["token"].count(".") == 1


def test_login_with_a_wrong_password_or_an_unknown_user_is_401(client):
    for response in (login(client, PATIENT, "wrong"), login(client, "P-NOBODY")):
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid_credentials"}


def test_me_returns_the_identity_of_the_token(client):
    response = client.get("/api/auth/me", headers=auth(client, NURSE))
    assert response.status_code == 200
    assert response.json() == {"user_id": NURSE, "role": "clinical_staff",
                               "display_name": "אחות מתאמת"}


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer "},
    {"Authorization": "Basic abc"},
    {"Authorization": "Bearer not-a-token"},
])
def test_a_missing_or_malformed_token_is_401(client, headers):
    response = client.get("/api/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "not_authenticated"}


def test_an_expired_token_is_401(client):
    stale = issue_token(PATIENT_USER, now=datetime.now(UTC) - timedelta(hours=9), secret=auth_secret())
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {stale}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "not_authenticated"}


def test_a_tampered_token_is_401(client):
    payload, signature = login(client, PATIENT).json()["token"].split(".")
    forged = issue_token(NURSE_USER, now=datetime.now(UTC), secret="another-secret").split(".")[0]
    for token in (f"{payload}.{signature[::-1]}", f"{forged}.{signature}"):
        response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json() == {"detail": "not_authenticated"}


# --- the patient's own cases ------------------------------------------------------------------

def test_a_patient_submits_a_request_and_lists_it(client):
    created = submit(client, PATIENT)
    assert created.status_code == 201
    view = created.json()
    assert view["status"] == "in_progress"  # no Orchestrator runs in this test app
    assert view["request_text"] == REQUEST
    assert view["missing_document_ids"] == [] and view["missing_document_request_template_id"] is None
    assert set(view) == {"case_id", "status", "created_at", "updated_at", "request_text",
                         "missing_document_ids", "missing_document_request_template_id", "message",
                         "history", "document_upload", "reply_request", "conversation", "instructions"}
    assert view["instructions"] is None  # not completed
    assert view["document_upload"] == "text"  # no document-service client in this test app
    assert [step["status"] for step in view["history"]] == ["received", "in_progress"]
    assert all(set(step) == {"status", "at"} for step in view["history"])

    listed = client.get("/api/patient/requests", headers=auth(client, PATIENT))
    assert listed.status_code == 200
    assert [item["case_id"] for item in listed.json()] == [view["case_id"]]

    one = client.get(f"/api/patient/requests/{view['case_id']}", headers=auth(client, PATIENT))
    assert one.status_code == 200 and one.json()["case_id"] == view["case_id"]


def test_a_patient_never_sees_another_patients_case(client):
    case_id = submit(client, PATIENT).json()["case_id"]
    other = auth(client, OTHER)
    assert client.get("/api/patient/requests", headers=other).json() == []
    response = client.get(f"/api/patient/requests/{case_id}", headers=other)
    assert response.status_code == 404 and response.json() == {"detail": "case_not_found"}
    upload = client.post(f"/api/patient/requests/{case_id}/documents", headers=other,
                         json={"document_id": "blood_test", "format": "pdf", "content": "normal"})
    assert upload.status_code == 404 and upload.json() == {"detail": "case_not_found"}


def test_an_unknown_case_is_404(client):
    response = client.get("/api/patient/requests/CASE-DOES-NOT-EXIST", headers=auth(client, PATIENT))
    assert response.status_code == 404 and response.json() == {"detail": "case_not_found"}


def test_an_unverified_patient_goes_into_review_and_sees_only_the_status(client):
    view = submit(client, "P-30000").json()
    assert view["status"] == "in_review"
    assert "escalation_kind" not in view and "reason" not in view  # §12.3: never shown to a patient


# --- validation ------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
def test_an_empty_or_oversized_request_is_422(client, text):
    assert submit(client, PATIENT, text).status_code == 422


def test_a_422_body_never_echoes_what_was_sent(client):
    """§12.3: FastAPI's default validation body carries `input` - the value that failed. A
    password, a request text or a document would travel back in the error; ours does not."""
    secret = "p" * 300
    response = client.post("/api/auth/login", json={"user_id": PATIENT, "password": secret})
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_body"}
    assert secret not in response.text and "input" not in response.text

    document = {"document_id": "blood_test", "format": "pdf", "content": "c" * 20001}
    case_id = submit(client, PATIENT).json()["case_id"]
    upload = client.post(f"/api/patient/requests/{case_id}/documents", json=document,
                         headers=auth(client, PATIENT))
    assert upload.status_code == 422
    assert upload.json() == {"detail": "invalid_body"}
    assert document["content"] not in upload.text


def test_a_request_of_the_maximum_length_is_accepted(client):
    assert submit(client, PATIENT, "x" * 2000).status_code == 201


# --- the patient-chosen appointment (sub-project 18, design D5) ----------------------------

def test_appointment_id_is_optional_and_stored_on_the_case(client, app_engine):
    response = client.post("/api/patient/requests", json={"text": REQUEST, "appointment_id": "APT-8391"},
                           headers=auth(client, PATIENT))
    assert response.status_code == 201
    case_id = response.json()["case_id"]
    with app_engine.connect() as conn:
        case = repository.load_case(conn, case_id)
    assert case.appointment_id == "APT-8391"


def test_appointment_id_is_absent_by_default(client, app_engine):
    case_id = submit(client, PATIENT).json()["case_id"]
    with app_engine.connect() as conn:
        case = repository.load_case(conn, case_id)
    assert case.appointment_id is None


def test_the_case_belongs_to_the_token_regardless_of_appointment_id(client, app_engine):
    """appointment_id never carries identity (§18.3) - each patient's own token still decides
    whose case it is, even when two patients happen to send the same id."""
    for patient in (PATIENT, OTHER):
        case_id = client.post("/api/patient/requests", json={"text": REQUEST, "appointment_id": "APT-8391"},
                              headers=auth(client, patient)).json()["case_id"]
        with app_engine.connect() as conn:
            case = repository.load_case(conn, case_id)
        assert (case.patient_id, case.appointment_id) == (patient, "APT-8391")


@pytest.mark.parametrize("appointment_id", ["", " ", "a b", "a" * 65, ".abc", "-abc", "_abc", "abc/def"])
def test_a_malformed_appointment_id_is_422(client, appointment_id):
    response = client.post("/api/patient/requests", json={"text": REQUEST, "appointment_id": appointment_id},
                           headers=auth(client, PATIENT))
    assert response.status_code == 422 and response.json() == {"detail": "invalid_body"}


@pytest.mark.parametrize("document", [
    {"document_id": "blood_test", "format": "tiff", "content": "normal"},
    {"document_id": "blood test", "format": "pdf", "content": "normal"},
    {"document_id": "", "format": "pdf", "content": "normal"},
    {"document_id": "b" * 65, "format": "pdf", "content": "normal"},
    {"document_id": "blood_test", "format": "pdf", "content": ""},
    {"document_id": "blood_test", "format": "pdf", "content": "c" * 20001},
    {"document_id": "blood_test", "content": "normal"},
])
def test_an_invalid_document_is_422(client, document):
    case_id = submit(client, PATIENT).json()["case_id"]
    response = client.post(f"/api/patient/requests/{case_id}/documents", json=document,
                           headers=auth(client, PATIENT))
    assert response.status_code == 422


def test_a_rejected_upload_still_answers_200_with_the_patient_view(client):
    """The case is in Classifying, so DOCUMENT_UPLOADED does not move it: the upload is
    tombstoned (D25) and the patient simply sees the unchanged view."""
    case_id = submit(client, PATIENT).json()["case_id"]
    response = client.post(f"/api/patient/requests/{case_id}/documents", headers=auth(client, PATIENT),
                           json={"document_id": "blood_test", "format": "pdf", "content": "normal"})
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"


# --- roles ------------------------------------------------------------------------------------

@pytest.mark.parametrize(("method", "path", "body"), [
    ("GET", "/api/staff/cases", None),
    ("GET", "/api/staff/cases/CASE-1", None),
    ("GET", "/api/staff/cases/CASE-1/audit", None),
    ("GET", "/api/staff/reviews", None),
    ("GET", "/api/staff/cases/CASE-1/context", None),
    ("POST", "/api/staff/cases/CASE-1/decision",
     {"decision": "resolve", "reason": "x", "shown_context_ref": "ctx-x"}),
    ("DELETE", "/api/staff/cases/CASE-1/data/DATA-1", None),
    ("POST", "/api/staff/cases/CASE-1/request",
     {"kind": "question", "template_id": "clarify_general", "reason": "x", "shown_context_ref": "ctx-x"}),
])
def test_a_patient_is_forbidden_on_staff_routes(client, method, path, body):
    response = client.request(method, path, json=body, headers=auth(client, PATIENT))
    assert response.status_code == 403 and response.json() == {"detail": "staff_only"}


def test_staff_are_forbidden_on_patient_routes(client):
    headers = auth(client, NURSE)
    assert client.get("/api/patient/requests", headers=headers).status_code == 403
    response = client.post("/api/patient/requests", json={"text": REQUEST}, headers=headers)
    assert response.status_code == 403 and response.json() == {"detail": "patients_only"}
