"""The server starts the Agent Orchestrator only when the Model Selector finds a provider (LLM design §3)."""
import logging

from fastapi.testclient import TestClient

from hospital_agent.api import app as app_module
from hospital_agent.llm.orchestrator import Orchestrator
from hospital_agent.llm.provider import FakeProvider
from hospital_agent.llm_costs import UsageRecorder


def test_the_server_starts_the_orchestrator_only_with_a_provider(monkeypatch, app_engine):
    monkeypatch.setenv("DATABASE_URL", app_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(app_module, "select_provider", lambda: None)
    with TestClient(app_module.create_app()) as client:
        assert client.get("/health").json()["orchestrator"] == "disabled: OPENAI_API_KEY is not set"
    monkeypatch.setattr(app_module, "select_provider", FakeProvider)
    with TestClient(app_module.create_app()) as client:
        assert client.get("/health").json() == {"status": "ok", "database": "ok", "orchestrator": "running",
                                                "llm": "unknown",
                                                "appointments": "mock", "documents": "mock"}


def test_a_missing_key_logs_an_error_at_startup(monkeypatch, app_engine, caplog):
    """Staff-fixes design Task 1, decision 4: a missing key stays fail-closed (the server still
    runs) but is now loud.

    Fix round 1, I1: `logging_setup.configure()` is stubbed out here so this test only checks
    that app.py logs the line - not that `configure()` wires up a handler (that is
    test_logging_setup.py's job). Without the stub, `configure()`'s `propagate=False` can flip
    on *after* pytest's own per-test log capture has already attached itself higher up, so the
    record never reaches it - the exact failure this test used to have when run alone."""
    monkeypatch.setenv("DATABASE_URL", app_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(app_module, "select_provider", lambda: None)
    monkeypatch.setattr(app_module.logging_setup, "configure", lambda: None)
    with caplog.at_level(logging.ERROR, logger="hospital_agent.api.app"):
        with TestClient(app_module.create_app()) as client:
            assert client.get("/health").json()["orchestrator"] == "disabled: OPENAI_API_KEY is not set"
    assert any(record.message == "OPENAI_API_KEY is not set: the Agent Orchestrator is disabled"
              for record in caplog.records)


def test_health_reports_llm_summary_only_not_the_code_or_timestamps(monkeypatch, app_engine):
    """Fix round 1, M6: `/health` is public, so it carries only "ok"/"error"/"unknown" - the
    error code and both timestamps stay on the staff-only GET /api/staff/system-status."""
    from hospital_agent.llm import telemetry
    from hospital_agent.llm.schemas import Call

    monkeypatch.setenv("DATABASE_URL", app_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(app_module, "select_provider", FakeProvider)
    with TestClient(app_module.create_app()) as client:
        assert client.get("/health").json()["llm"] == "unknown"
        telemetry.record(Call.INTENT, "fake", 1, "ok")
        assert client.get("/health").json()["llm"] == "ok"
        telemetry.record(Call.INTENT, "fake", 1, "api:AuthenticationError")
        body = client.get("/health").json()
        assert body["llm"] == "error"
        assert "last_error" not in body and "last_ok_at" not in body  # detail is staff-only
        telemetry.record(Call.INTENT, "fake", 1, "ok")
        assert client.get("/health").json()["llm"] == "ok"


def test_the_app_exposes_the_running_orchestrator(monkeypatch, app_engine):
    monkeypatch.setenv("DATABASE_URL", app_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(app_module, "select_provider", lambda: None)
    with TestClient(app_module.create_app()) as client:
        assert client.app.state.orchestrator is None
    monkeypatch.setattr(app_module, "select_provider", FakeProvider)
    with TestClient(app_module.create_app()) as client:
        assert isinstance(client.app.state.orchestrator, Orchestrator)
        # Sub-project 19: the running orchestrator records usage on the app's own engine
        recorder = client.app.state.orchestrator.recorder
        assert isinstance(recorder, UsageRecorder) and recorder.engine is client.app.state.engine


def test_the_server_uses_the_appointment_service_when_configured(monkeypatch, app_engine):
    """design §2.11: with a URL and a key, /health names the real appointment-service. No
    case exists yet, so nothing calls it in this window - only /health is checked."""
    monkeypatch.setenv("DATABASE_URL", app_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(app_module, "select_provider", FakeProvider)
    monkeypatch.setenv("APPOINTMENT_SERVICE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("APPOINTMENT_API_KEY", "dummy")
    with TestClient(app_module.create_app()) as client:
        assert client.get("/health").json() == {"status": "ok", "database": "ok", "orchestrator": "running",
                                                "llm": "unknown",
                                                "appointments": "appointment-service", "documents": "mock"}


def test_the_server_refuses_to_start_with_a_url_and_no_key(monkeypatch, app_engine):
    monkeypatch.setenv("DATABASE_URL", app_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(app_module, "select_provider", FakeProvider)
    monkeypatch.setenv("APPOINTMENT_SERVICE_URL", "http://127.0.0.1:1")
    with TestClient(app_module.create_app()) as client:
        body = client.get("/health").json()
        assert body["orchestrator"] == "disabled: APPOINTMENT_API_KEY is not set"
        assert "appointments" not in body


def test_the_server_refuses_to_start_with_a_document_url_and_no_key(monkeypatch, app_engine):
    monkeypatch.setenv("DATABASE_URL", app_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(app_module, "select_provider", FakeProvider)
    monkeypatch.setenv("DOCUMENT_SERVICE_URL", "http://127.0.0.1:1")
    with TestClient(app_module.create_app()) as client:
        body = client.get("/health").json()
        assert body["orchestrator"] == "disabled: DOCUMENT_API_KEY is not set"
        assert "appointments" not in body and "documents" not in body
