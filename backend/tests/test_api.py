"""The app itself: /health (public) and the shape of the route table.

The Case Monitor routes moved behind staff auth - they are tested in tests/test_api_staff.py.
"""
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import ProgrammingError

from hospital_agent.api import deps
from hospital_agent.api.app import create_app


class _BrokenConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, *args, **kwargs):
        # A SQLAlchemyError that is NOT an OperationalError (e.g. a bad migration state).
        raise ProgrammingError("SELECT 1", {}, Exception('relation "x" does not exist'))


class _BrokenEngine:
    def connect(self):
        return _BrokenConnection()


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


def test_health_is_503_for_any_sqlalchemy_error_not_just_operational_error():
    """M3: a non-OperationalError SQLAlchemyError (e.g. a bad migration state) is also 503."""
    with TestClient(create_app(_BrokenEngine())) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_the_routes_never_carry_a_patient_id(client):
    """§12.3: an id in a path would leak into the application log. The patient's own id
    comes from the token, and a case is addressed by case_id."""
    paths = [getattr(route, "path", "") for route in client.app.routes]
    assert not any("patient_id" in path for path in paths)


# Every /api route except the login itself, with a body where one is needed.
PROTECTED = [
    ("GET", "/api/auth/me", None),
    ("GET", "/api/patient/requests", None),
    ("POST", "/api/patient/requests", {"text": "hello"}),
    ("GET", "/api/patient/requests/CASE-1", None),
    ("POST", "/api/patient/requests/CASE-1/documents",
     {"document_id": "blood_test", "format": "pdf", "content": "x"}),
    ("GET", "/api/staff/cases", None),
    ("GET", "/api/staff/cases/CASE-1", None),
    ("GET", "/api/staff/cases/CASE-1/audit", None),
    ("GET", "/api/staff/system-status", None),
    ("GET", "/api/staff/reviews", None),
    ("GET", "/api/staff/cases/CASE-1/context", None),
    ("POST", "/api/staff/cases/CASE-1/decision",
     {"decision": "resolve", "reason": "x", "shown_context_ref": "ctx-x"}),
    ("DELETE", "/api/staff/cases/CASE-1/data/DATA-1", None),
]


def test_every_api_route_requires_a_token(client):
    assert client.get("/health").status_code == 200
    for method, path, body in PROTECTED:
        response = client.request(method, path, json=body)
        assert response.status_code == 401, (method, path)
        assert response.json() == {"detail": "not_authenticated"}, (method, path)


def test_every_api_route_declares_an_authentication_dependency(client):
    """A new route cannot become public by forgetting a check: the dependency is the check,
    and only the login is allowed to have none."""
    authenticators = {deps.current_principal, deps.require_patient, deps.require_staff}
    for route in client.app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
            continue
        if route.path == "/api/auth/login":
            continue
        assert authenticators & set(_dependency_calls(route.dependant)), route.path


def _dependency_calls(dependant):
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependency_calls(sub)


def test_cors_allows_the_ui_dev_server(client):
    response = client.options("/api/auth/login", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
