"""/api/admin/metrics (sub-project 14, design §5): admin_staff only, a validated window, and
aggregates without a single patient identifier."""
import logging

import pytest
from fastapi.testclient import TestClient

from hospital_agent import metrics
from hospital_agent.api.app import create_app
from hospital_agent.auth import demo_password
from tests.metrics_seed import add_case, add_row, at

NURSE, ADMIN, PATIENT = "coordinator_nurse", "admin_coordinator", "P-10041"
FROM, TO = "2026-09-01T00:00:00+00:00", "2026-09-02T00:00:00+00:00"
GROUPS = {"window", "generated_at", "flow", "human_load", "tools", "patient_sla", "policy", "llm"}


@pytest.fixture
def client(app_engine):
    with TestClient(create_app(app_engine)) as test_client:
        yield test_client


def headers(client, user_id):
    token = client.post("/api/auth/login", json={"user_id": user_id, "password": demo_password()}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def get(client, auth=None, **params):
    return client.get("/api/admin/metrics", params={"from": FROM, "to": TO, **params}, headers=auth or {})


def test_no_token_is_401(client):
    response = get(client)
    assert (response.status_code, response.json()["detail"]) == (401, "not_authenticated")


@pytest.mark.parametrize("user_id", [PATIENT, NURSE])
def test_anyone_but_admin_staff_is_403(client, user_id):
    response = get(client, headers(client, user_id))
    assert (response.status_code, response.json()["detail"]) == (403, "admin_only")


def test_admin_staff_gets_every_group(client):
    response = get(client, headers(client, ADMIN))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == GROUPS
    assert body["window"] == {"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"}
    # create_app(engine) starts no orchestrator, so neither source is reported
    assert body["tools"]["sources"] == {"appointments": None, "documents": None}


def test_a_missing_parameter_is_the_apps_invalid_body(client):
    response = client.get("/api/admin/metrics", params={"from": FROM}, headers=headers(client, ADMIN))
    assert (response.status_code, response.json()["detail"]) == (422, "invalid_body")


@pytest.mark.parametrize("params, code", [
    ({"from": "yesterday"}, "invalid_range"),
    ({"from": "2026-09-01T00:00:00"}, "invalid_range"),  # no time zone
    ({"from": TO, "to": FROM}, "invalid_range"),
    ({"to": "2026-12-01T00:00:01+00:00"}, "range_too_large"),
])
def test_a_bad_window_is_422_with_its_code(client, params, code):
    response = get(client, headers(client, ADMIN), **params)
    assert (response.status_code, response.json()["detail"]) == (422, code)


def test_a_timeout_is_503_and_never_a_partial_answer(client, monkeypatch):
    def timed_out(engine, window, sources):
        raise metrics.MetricsUnavailable("statement_timeout")

    monkeypatch.setattr(metrics, "compute", timed_out)
    response = get(client, headers(client, ADMIN))
    assert (response.status_code, response.json()["detail"]) == (503, "metrics_unavailable")


def test_the_answer_and_the_log_carry_no_patient_identifier(client, app_engine, caplog):
    with app_engine.begin() as conn:
        add_case(conn, "CASE-PRIVATE-1", created_at=at(1), state="AwaitingHumanReview",
                 escalation_kind="MedicalQuestion", patient_id="P-20000")
        add_row(conn, "CASE-PRIVATE-1", "MEDICAL_QUESTION_DETECTED", at=at(2), before="Classifying",
                after="AwaitingHumanReview", patient_id="P-20000")
    auth = headers(client, ADMIN)
    with caplog.at_level(logging.INFO, logger="hospital_agent.api.routes_admin"):
        response = get(client, auth)
    assert response.status_code == 200
    assert response.json()["flow"]["opened"] == 1
    for identifier in ("CASE-PRIVATE-1", "P-20000"):
        assert identifier not in response.text
        assert identifier not in caplog.text
    assert "admin_coordinator" in caplog.text
