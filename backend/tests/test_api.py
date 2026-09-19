"""Read-only Case Monitor API (design §9)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from hospital_agent.api.app import create_app
from tests.driver import Driver


@pytest.fixture
def client(app_engine):
    with TestClient(create_app(app_engine)) as test_client:
        yield test_client


def test_health_reports_the_database(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_is_503_when_the_database_is_unreachable():
    unreachable = create_engine("postgresql+psycopg://nobody:nothing@localhost:1/none")
    with TestClient(create_app(unreachable)) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_cases_list_and_filter_by_state(client, sm, app_engine):
    classified = Driver(sm, app_engine)
    classified.to_classified()
    received = Driver(sm, app_engine, patient_id="P-2")
    received.submit()

    all_cases = client.get("/cases").json()
    assert {c["case_id"] for c in all_cases} == {classified.case_id, received.case_id}
    assert set(all_cases[0]) == {"case_id", "state", "escalation_kind", "updated_at"}

    only_received = client.get("/cases", params={"state": "Received"}).json()
    assert [c["case_id"] for c in only_received] == [received.case_id]

    assert client.get("/cases", params={"state": "Sleeping"}).status_code == 422


def test_case_detail_and_404(client, sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    detail = client.get(f"/cases/{d.case_id}").json()
    assert (detail["state"], detail["state_version"], detail["identity_verified"]) == ("Classified", 3, True)
    assert detail["safety_level"] == "MediumRisk"
    assert client.get("/cases/CASE-NOPE").status_code == 404


def test_case_audit_is_the_ordered_trace(client, sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    audit = client.get(f"/cases/{d.case_id}/audit").json()
    assert [row["event"] for row in audit] == ["REQUEST_SUBMITTED", "REQUEST_VALIDATED", "INTENT_CLASSIFIED"]
    assert [row["audit_id"] for row in audit] == sorted(row["audit_id"] for row in audit)
    assert client.get("/cases/CASE-NOPE/audit").status_code == 404


def test_patient_id_is_not_part_of_any_route(client):
    paths = [route.path for route in client.app.routes]
    assert not any("patient" in path for path in paths)
