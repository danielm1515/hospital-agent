"""The three §0 scenarios, and the P-30000 identity path, driven through the HTTP API only.

The app gets an injected Orchestrator (never started - the test steps it with run_case()
after every patient or staff call, exactly where the running server would have been woken).
Everything else is the real system: the State Manager, the Policy Service, the Readiness
Check, the Temporal Monitor, the Tool Executor and the deterministic FakeProvider.
"""
import pytest
from fastapi.testclient import TestClient

from hospital_agent.api.app import create_app
from hospital_agent.auth import demo_password
from hospital_agent.execution.gateway import MockGateway
from hospital_agent.llm.orchestrator import Orchestrator
from hospital_agent.llm.provider import FakeProvider
from hospital_agent.session import MISSING_DOCUMENT_TEMPLATE_ID

REQUEST = "When is my appointment and which documents do I need?"
MEDICAL = "Should I stop taking my blood thinner?"
DOCUMENT = {"document_id": "blood_test", "format": "pdf", "content": "Blood test results: normal."}
PATIENT, NURSE = "P-10041", "coordinator_nurse"


class Api:
    """A TestClient plus the injected Orchestrator the test steps itself."""

    def __init__(self, client: TestClient, orchestrator: Orchestrator) -> None:
        self.client, self.orchestrator = client, orchestrator

    def headers(self, user_id):
        body = {"user_id": user_id, "password": demo_password()}
        token = self.client.post("/api/auth/login", json=body).json()["token"]
        return {"Authorization": f"Bearer {token}"}

    def submit(self, text=REQUEST, user_id=PATIENT):
        return self.client.post("/api/patient/requests", json={"text": text},
                                headers=self.headers(user_id)).json()

    def upload(self, case_id, user_id=PATIENT):
        return self.client.post(f"/api/patient/requests/{case_id}/documents", json=DOCUMENT,
                                headers=self.headers(user_id)).json()

    def view(self, case_id, user_id=PATIENT):
        return self.client.get(f"/api/patient/requests/{case_id}", headers=self.headers(user_id)).json()

    def queue(self):
        return self.client.get("/api/staff/reviews", headers=self.headers(NURSE)).json()

    def decide(self, case_id, decision, **fields):
        staff = self.headers(NURSE)
        ref = self.client.get(f"/api/staff/cases/{case_id}/context", headers=staff).json()["shown_context_ref"]
        body = {"decision": decision, "reason": "reviewed by staff", "shown_context_ref": ref, **fields}
        return self.client.post(f"/api/staff/cases/{case_id}/decision", json=body, headers=staff)

    def run(self, case_id):
        return self.orchestrator.run_case(case_id)


@pytest.fixture
def api(app_engine, sm):
    def build(gateway=None):
        orchestrator = Orchestrator(sm, FakeProvider(), gateway or MockGateway())
        client = TestClient(create_app(app_engine, orchestrator=orchestrator))
        client.__enter__()
        built.append((client, orchestrator))
        return Api(client, orchestrator)

    built = []
    yield build
    for client, orchestrator in built:
        client.__exit__(None, None, None)
        orchestrator.close()


def test_scenario_1_a_missing_document_then_the_status_message(api):
    app = api()
    case_id = app.submit()["case_id"]
    app.run(case_id)

    waiting = app.view(case_id)
    assert waiting["status"] == "needs_document"
    assert waiting["missing_document_ids"] == ["blood_test"]
    assert waiting["missing_document_request_template_id"] == MISSING_DOCUMENT_TEMPLATE_ID
    assert waiting["message"] is None

    app.upload(case_id)
    app.run(case_id)

    done = app.view(case_id)
    assert done["status"] == "completed"
    assert "INSTR-PREP-COLONOSCOPY" in done["message"]


def test_scenario_2_a_medical_question_is_queued_and_resolved(api):
    app = api()
    case_id = app.submit(MEDICAL)["case_id"]
    app.run(case_id)

    assert app.view(case_id)["status"] == "in_review"
    [item] = app.queue()
    assert (item["case_id"], item["escalation_kind"]) == (case_id, "MedicalQuestion")
    assert item["allowed_decisions"] == ["resolve", "reject"]

    assert app.decide(case_id, "resolve").json() == {"case_id": case_id, "state": "Completed"}
    app.run(case_id)

    closed = app.view(case_id)
    assert closed["status"] == "closed"  # Completed without a delivery: nothing was sent
    assert closed["message"] is None
    assert app.queue() == []


def test_scenario_3_a_technical_failure_is_approved_and_the_case_finishes(api):
    app = api(MockGateway(failures={"CheckDocuments": 3}))
    case_id = app.submit()["case_id"]
    app.run(case_id)

    [item] = app.queue()
    assert (item["case_id"], item["escalation_kind"]) == (case_id, "RetryExhausted")
    assert item["allowed_decisions"] == ["approve", "resolve", "reject"]
    assert item["required_fields"] == []
    assert app.view(case_id)["status"] == "in_review"

    assert app.decide(case_id, "approve").json() == {"case_id": case_id, "state": "Planning"}
    app.run(case_id)
    assert app.view(case_id)["status"] == "needs_document"

    app.upload(case_id)
    app.run(case_id)
    assert app.view(case_id)["status"] == "completed"


def test_an_unverified_patient_is_approved_by_staff_and_the_case_goes_on(api):
    app = api()
    case_id = app.submit(user_id="P-30000")["case_id"]
    assert app.view(case_id, "P-30000")["status"] == "in_review"
    [item] = app.queue()
    assert item["escalation_kind"] == "PatientVerificationFailed"
    assert item["required_fields"] == ["verified_identity_ref"]

    response = app.decide(case_id, "approve", verified_identity_ref="ID-DESK-17")
    assert response.status_code == 200 and response.json()["state"] == "Classifying"

    app.run(case_id)
    assert app.view(case_id, "P-30000")["status"] == "needs_document"
