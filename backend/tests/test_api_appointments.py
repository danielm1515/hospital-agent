"""Sub-project 16 (design D5, D6, D9): the patient's and the staff's appointment routes."""
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from hospital_agent.api.app import create_app
from hospital_agent.appointment_list import (
    Appointment,
    AppointmentList,
    AppointmentsUnavailable,
    PatientNotFound,
)
from hospital_agent.auth import demo_password

PATIENT, OTHER, NURSE, ADMIN = "P-10041", "P-20000", "coordinator_nurse", "admin_coordinator"
AT = datetime(2026, 10, 3, 7, 30, tzinfo=UTC)
ONE = Appointment("APT-8391", AT, "Neurology", "Dr. Cohen", "Building B, Floor 2", "Scheduled", ("CBC", "ECG"))


class FakeList:
    def __init__(self, result=None, raises=None):
        self.result = result or AppointmentList((ONE,), False)
        self.raises = raises
        self.calls = []

    def list(self, patient_id, start, end):
        self.calls.append((patient_id, start, end))
        if self.raises:
            raise self.raises
        return self.result


def make(app_engine, fake):
    return TestClient(create_app(app_engine, appointment_list=fake))


def auth(client, user_id):
    token = client.post("/api/auth/login", json={"user_id": user_id, "password": demo_password()}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def open_case(client, user_id=PATIENT):
    response = client.post("/api/patient/requests", json={"text": "מתי התור שלי?"}, headers=auth(client, user_id))
    return response.json()["case_id"]


def test_the_patient_gets_their_own_list_by_token(app_engine):
    fake = FakeList()
    with make(app_engine, fake) as client:
        response = client.get("/api/patient/appointments", headers=auth(client, PATIENT),
                              params={"from": "2026-10-01T00:00:00+03:00", "to": "2026-11-01T00:00:00+03:00",
                                      "patient_id": OTHER})  # a query patient_id is ignored
    assert response.status_code == 200
    body = response.json()
    assert fake.calls[0][0] == PATIENT
    assert body["appointments"][0] == {"appointment_id": "APT-8391", "appointment_at": "2026-10-03T07:30:00Z",
                                       "department": "Neurology", "doctor_name": "Dr. Cohen",
                                       "location": "Building B, Floor 2", "status": "Scheduled",
                                       "required_documents": ["CBC", "ECG"]}
    assert body["truncated"] is False
    # Both bounds carry the same +03:00 offset over the same one-month span, so both shift by
    # the same 3 hours: 2026-10-01T00:00:00+03:00 -> 2026-09-30T21:00:00Z and
    # 2026-11-01T00:00:00+03:00 -> 2026-10-31T21:00:00Z (the brief's own "22:00:00Z" for the
    # second bound does not hold under exact UTC arithmetic - verified with
    # datetime.astimezone(UTC) directly; see task-3-report.md).
    assert (body["from"], body["to"]) == ("2026-09-30T21:00:00Z", "2026-10-31T21:00:00Z")


def test_the_default_window_is_now_plus_30_days(app_engine):
    fake = FakeList()
    with make(app_engine, fake) as client:
        before = datetime.now(UTC)
        client.get("/api/patient/appointments", headers=auth(client, PATIENT))
    _, start, end = fake.calls[0]
    assert before - timedelta(seconds=5) <= start <= datetime.now(UTC)
    assert end - start == timedelta(days=30)


@pytest.mark.parametrize("params", [
    {"from": "2026-10-01T00:00:00", "to": "2026-11-01T00:00:00+03:00"},      # naive
    {"from": "2026-11-01T00:00:00+03:00", "to": "2026-10-01T00:00:00+03:00"},  # reversed
    {"from": "2026-01-01T00:00:00+03:00", "to": "2027-01-03T00:00:00+03:00"},  # over 366 days
    {"from": "tomorrow", "to": "2026-11-01T00:00:00+03:00"},
])
def test_a_bad_window_is_422_before_any_call(app_engine, params):
    fake = FakeList()
    with make(app_engine, fake) as client:
        response = client.get("/api/patient/appointments", headers=auth(client, PATIENT), params=params)
    assert (response.status_code, response.json()["detail"]) == (422, "invalid_range")
    assert fake.calls == []


@pytest.mark.parametrize("params", [
    {"from": "9999-12-31T00:00:00Z"},                                          # from alone overflows +30d
    {"to": "0001-01-02T00:00:00Z"},                                            # to alone underflows -30d
    {"from": "0001-01-01T00:00:00+03:00", "to": "0001-01-02T00:00:00+03:00"},  # from underflows converting to UTC
    {"from": "9999-12-30T00:00:00-03:00", "to": "9999-12-31T23:00:00-03:00"},  # to overflows converting to UTC
])
def test_an_edge_year_window_is_422_not_500(app_engine, params):
    """Fix round 1, item 1: astimezone()/the default-span arithmetic can raise OverflowError
    at the edge of datetime's range - it must fail closed as 422 invalid_range, never a 500,
    and never reach the appointment-service."""
    fake = FakeList()
    with make(app_engine, fake) as client:
        response = client.get("/api/patient/appointments", headers=auth(client, PATIENT), params=params)
    assert (response.status_code, response.json()["detail"]) == (422, "invalid_range")
    assert fake.calls == []


def test_from_equal_to_is_422(app_engine):
    fake = FakeList()
    with make(app_engine, fake) as client:
        response = client.get("/api/patient/appointments", headers=auth(client, PATIENT),
                              params={"from": "2026-10-01T00:00:00+00:00", "to": "2026-10-01T00:00:00+00:00"})
    assert (response.status_code, response.json()["detail"]) == (422, "invalid_range")
    assert fake.calls == []


def test_exactly_366_days_is_ok_366_days_plus_a_second_is_not(app_engine):
    fake = FakeList()
    with make(app_engine, fake) as client:
        ok = client.get("/api/patient/appointments", headers=auth(client, PATIENT),
                        params={"from": "2026-01-01T00:00:00+00:00", "to": "2027-01-02T00:00:00+00:00"})
        too_long = client.get("/api/patient/appointments", headers=auth(client, PATIENT),
                              params={"from": "2026-01-01T00:00:00+00:00", "to": "2027-01-02T00:00:01+00:00"})
    assert ok.status_code == 200
    assert (too_long.status_code, too_long.json()["detail"]) == (422, "invalid_range")


def test_not_configured_wins_over_a_bad_window(app_engine):
    """Fix round 1, item 6: `client is None` is checked before the window, so an unconfigured
    server answers the same 404 regardless of what the query looks like."""
    with make(app_engine, None) as client:
        response = client.get("/api/patient/appointments", headers=auth(client, PATIENT),
                              params={"from": "2026-11-01T00:00:00+03:00", "to": "2026-10-01T00:00:00+03:00"})
    assert (response.status_code, response.json()["detail"]) == (404, "appointments_not_enabled")


def test_appointment_at_goes_out_in_utc_whatever_zone_the_service_used(app_engine):
    """Fix round 1, item 2: the appointment-service's own `appointment_at` is normalised to
    UTC before the answer goes out, exactly like the window bounds."""
    local = Appointment("APT-1", datetime(2026, 10, 3, 10, 30, tzinfo=ZoneInfo("Asia/Jerusalem")),
                        "Neurology", "Dr. Cohen", "Building B", "Scheduled", ())
    fake = FakeList(result=AppointmentList((local,), False))
    with make(app_engine, fake) as client:
        response = client.get("/api/patient/appointments", headers=auth(client, PATIENT))
    assert response.json()["appointments"][0]["appointment_at"] == "2026-10-03T07:30:00Z"


def test_one_end_alone_spans_30_days_from_it(app_engine):
    fake = FakeList()
    with make(app_engine, fake) as client:
        client.get("/api/patient/appointments", headers=auth(client, PATIENT),
                   params={"from": "2026-10-01T00:00:00+00:00"})
        client.get("/api/patient/appointments", headers=auth(client, PATIENT),
                   params={"to": "2026-10-31T00:00:00+00:00"})
    assert fake.calls[0][2] - fake.calls[0][1] == timedelta(days=30)
    assert fake.calls[1][1] == datetime(2026, 10, 1, tzinfo=UTC)


@pytest.mark.parametrize("raises, status, code", [
    (PatientNotFound(), 404, "patient_not_found"),
    (AppointmentsUnavailable("no_answer"), 503, "appointments_unavailable"),
    (AppointmentsUnavailable("status_401"), 503, "appointments_unavailable"),
    (AppointmentsUnavailable("invalid_response"), 503, "appointments_unavailable"),
])
def test_each_failure_is_one_code(app_engine, raises, status, code):
    with make(app_engine, FakeList(raises=raises)) as client:
        response = client.get("/api/patient/appointments", headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (status, code)


def test_a_bad_patient_id_is_503_not_500(app_engine):
    """Controller ruling 1: the client's ValueError (its own patient_id pattern check) must
    never surface as a 500 - it is fail-closed the same as AppointmentsUnavailable, and the
    application log names the failure, never the patient_id."""
    with make(app_engine, FakeList(raises=ValueError("invalid patient_id"))) as client:
        response = client.get("/api/patient/appointments", headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (503, "appointments_unavailable")


def test_not_configured_is_404_not_enabled(app_engine):
    with make(app_engine, None) as client:
        response = client.get("/api/patient/appointments", headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (404, "appointments_not_enabled")


def test_the_patient_route_is_patients_only(app_engine):
    with make(app_engine, FakeList()) as client:
        response = client.get("/api/patient/appointments", headers=auth(client, NURSE))
        anonymous = client.get("/api/patient/appointments")
    assert (response.status_code, response.json()["detail"]) == (403, "patients_only")
    assert anonymous.status_code == 401


@pytest.mark.parametrize("staff", [NURSE, ADMIN])
def test_staff_read_the_cases_patient(app_engine, staff):
    fake = FakeList()
    with make(app_engine, fake) as client:
        case_id = open_case(client, OTHER)
        response = client.get(f"/api/staff/cases/{case_id}/appointments", headers=auth(client, staff))
    assert response.status_code == 200
    assert fake.calls[0][0] == OTHER


def test_staff_route_passes_the_window_through(app_engine):
    fake = FakeList()
    with make(app_engine, fake) as client:
        case_id = open_case(client, OTHER)
        client.get(f"/api/staff/cases/{case_id}/appointments", headers=auth(client, NURSE),
                   params={"from": "2026-10-01T00:00:00+00:00", "to": "2026-10-05T00:00:00+00:00"})
    assert fake.calls[0] == (OTHER, datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 5, tzinfo=UTC))


def test_staff_route_bad_window_is_422(app_engine):
    with make(app_engine, FakeList()) as client:
        case_id = open_case(client)
        response = client.get(f"/api/staff/cases/{case_id}/appointments", headers=auth(client, NURSE),
                              params={"from": "2026-11-01T00:00:00+03:00", "to": "2026-10-01T00:00:00+03:00"})
    assert (response.status_code, response.json()["detail"]) == (422, "invalid_range")


def test_staff_route_not_configured_is_404_not_enabled(app_engine):
    with make(app_engine, None) as client:
        case_id = open_case(client)
        response = client.get(f"/api/staff/cases/{case_id}/appointments", headers=auth(client, NURSE))
    assert (response.status_code, response.json()["detail"]) == (404, "appointments_not_enabled")


def test_the_staff_route_is_staff_only_and_knows_the_case(app_engine):
    with make(app_engine, FakeList()) as client:
        case_id = open_case(client)
        patient = client.get(f"/api/staff/cases/{case_id}/appointments", headers=auth(client, PATIENT))
        missing = client.get("/api/staff/cases/C-NOPE/appointments", headers=auth(client, NURSE))
    assert (patient.status_code, patient.json()["detail"]) == (403, "staff_only")
    assert (missing.status_code, missing.json()["detail"]) == (404, "case_not_found")


def test_the_staff_route_leaves_the_decision_binding_alone(app_engine):
    with make(app_engine, FakeList()) as client:
        case_id = open_case(client)
        nurse = auth(client, NURSE)
        before = client.get(f"/api/staff/cases/{case_id}/context", headers=nurse).json()["shown_context_ref"]
        client.get(f"/api/staff/cases/{case_id}/appointments", headers=nurse)
        after = client.get(f"/api/staff/cases/{case_id}/context", headers=nurse).json()["shown_context_ref"]
    assert before == after


def test_the_log_never_names_the_patient(app_engine, caplog):
    with caplog.at_level(logging.DEBUG, logger="hospital_agent.api.appointments"):
        with make(app_engine, FakeList(raises=AppointmentsUnavailable("no_answer"))) as client:
            client.get("/api/patient/appointments", headers=auth(client, PATIENT))
    assert "appointment list: no_answer in" in caplog.text
    assert PATIENT not in caplog.text and "APT-" not in caplog.text


def test_the_log_never_names_the_patient_on_success(app_engine, caplog):
    with caplog.at_level(logging.DEBUG, logger="hospital_agent.api.appointments"):
        with make(app_engine, FakeList()) as client:
            client.get("/api/patient/appointments", headers=auth(client, PATIENT))
    assert "appointment list: ok in" in caplog.text
    assert PATIENT not in caplog.text and "APT-" not in caplog.text


def test_the_log_never_names_the_patient_on_patient_not_found(app_engine, caplog):
    with caplog.at_level(logging.DEBUG, logger="hospital_agent.api.appointments"):
        with make(app_engine, FakeList(raises=PatientNotFound())) as client:
            client.get("/api/patient/appointments", headers=auth(client, PATIENT))
    assert "appointment list: patient_not_found in" in caplog.text
    assert PATIENT not in caplog.text and "APT-" not in caplog.text
