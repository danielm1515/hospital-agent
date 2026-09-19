"""The app itself: /health (public) and the shape of the route table.

The Case Monitor routes moved behind staff auth - they are tested in tests/test_api_staff.py.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import ProgrammingError

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


def test_the_public_surface_is_health_and_login_only(client):
    assert client.get("/health").status_code == 200
    for path in ("/api/patient/requests", "/api/staff/cases", "/api/staff/reviews", "/api/auth/me"):
        assert client.get(path).status_code == 401, path


def test_cors_allows_the_ui_dev_server(client):
    response = client.options("/api/auth/login", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
