"""The server starts the Agent Orchestrator only when the Model Selector finds a provider (LLM design §3)."""
from fastapi.testclient import TestClient

from hospital_agent.api import app as app_module
from hospital_agent.llm.orchestrator import Orchestrator
from hospital_agent.llm.provider import FakeProvider


def test_the_server_starts_the_orchestrator_only_with_a_provider(monkeypatch, app_engine):
    monkeypatch.setenv("DATABASE_URL", app_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(app_module, "select_provider", lambda: None)
    with TestClient(app_module.create_app()) as client:
        assert client.get("/health").json()["orchestrator"] == "disabled: OPENAI_API_KEY is not set"
    monkeypatch.setattr(app_module, "select_provider", FakeProvider)
    with TestClient(app_module.create_app()) as client:
        assert client.get("/health").json() == {"status": "ok", "database": "ok", "orchestrator": "running",
                                                "appointments": "mock", "documents": "mock"}


def test_the_app_exposes_the_running_orchestrator(monkeypatch, app_engine):
    monkeypatch.setenv("DATABASE_URL", app_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(app_module, "select_provider", lambda: None)
    with TestClient(app_module.create_app()) as client:
        assert client.app.state.orchestrator is None
    monkeypatch.setattr(app_module, "select_provider", FakeProvider)
    with TestClient(app_module.create_app()) as client:
        assert isinstance(client.app.state.orchestrator, Orchestrator)


def test_the_server_uses_the_appointment_service_when_configured(monkeypatch, app_engine):
    """design §2.11: with a URL and a key, /health names the real appointment-service. No
    case exists yet, so nothing calls it in this window - only /health is checked."""
    monkeypatch.setenv("DATABASE_URL", app_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(app_module, "select_provider", FakeProvider)
    monkeypatch.setenv("APPOINTMENT_SERVICE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("APPOINTMENT_API_KEY", "dummy")
    with TestClient(app_module.create_app()) as client:
        assert client.get("/health").json() == {"status": "ok", "database": "ok", "orchestrator": "running",
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
