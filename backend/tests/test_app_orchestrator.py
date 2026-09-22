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
                                                "appointments": "mock"}


def test_the_app_exposes_the_running_orchestrator(monkeypatch, app_engine):
    monkeypatch.setenv("DATABASE_URL", app_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(app_module, "select_provider", lambda: None)
    with TestClient(app_module.create_app()) as client:
        assert client.app.state.orchestrator is None
    monkeypatch.setattr(app_module, "select_provider", FakeProvider)
    with TestClient(app_module.create_app()) as client:
        assert isinstance(client.app.state.orchestrator, Orchestrator)
