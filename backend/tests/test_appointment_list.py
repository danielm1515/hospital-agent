"""Sub-project 16 (design D4, D7): the appointment list client - the contract or nothing."""
import json
from datetime import UTC, datetime

import pytest

from hospital_agent.appointment_list import (
    AppointmentListClient,
    AppointmentsUnavailable,
    PatientNotFound,
    build_list_client,
    map_answer,
)
from hospital_agent.execution.http import MAX_BODY_BYTES, HttpResponse

ONE = {"appointment_id": "APT-8391", "patient_id": "P-10041", "department": "Neurology",
       "doctor_name": "Dr. Cohen", "appointment_at": "2026-10-03T10:30:00+03:00",
       "location": "Building B, Floor 2", "status": "Scheduled", "required_documents": ["ECG", "CBC"]}


def answer(body, status=200):
    return HttpResponse(status, json.dumps(body).encode())


def test_a_valid_answer_is_parsed():
    result = map_answer(answer({"appointments": [ONE, {**ONE, "appointment_id": "APT-2", "status": "Cancelled",
                                                      "doctor_name": None, "location": None,
                                                      "required_documents": []}], "truncated": False}))
    first, second = result.appointments
    assert first.appointment_at == datetime(2026, 10, 3, 7, 30, tzinfo=UTC)
    assert first.required_documents == ("CBC", "ECG")
    assert (second.status, second.doctor_name, second.location) == ("Cancelled", None, None)
    assert result.truncated is False


def test_patient_not_found_is_its_own_answer():
    with pytest.raises(PatientNotFound):
        map_answer(answer({"error": "patient_not_found"}, status=404))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503, 504])
def test_any_other_status_is_unavailable(status):
    with pytest.raises(AppointmentsUnavailable) as caught:
        map_answer(answer({"error": "x"}, status=status))
    assert caught.value.code == ("invalid_response" if status == 404 else f"status_{status}")


@pytest.mark.parametrize("body", [
    [],                                                   # not a dict
    {"appointments": "x", "truncated": False},            # not a list
    {"appointments": [ONE]},                              # no truncated
    {"appointments": [ONE], "truncated": "no"},           # truncated not a bool
    {"appointments": [1], "truncated": False},            # an item that is not a dict
    {"appointments": [{**ONE, "appointment_at": "2026-10-03T10:30:00"}], "truncated": False},  # naive
    {"appointments": [{**ONE, "appointment_at": "soon"}], "truncated": False},
    {"appointments": [{**ONE, "status": "Moved"}], "truncated": False},
    {"appointments": [{**ONE, "appointment_id": ""}], "truncated": False},
    {"appointments": [{**ONE, "department": 7}], "truncated": False},
    {"appointments": [{**ONE, "doctor_name": 7}], "truncated": False},
    {"appointments": [{**ONE, "required_documents": ["CBC", ""]}], "truncated": False},
    {"appointments": [{**ONE, "required_documents": "CBC"}], "truncated": False},
])
def test_anything_but_the_contract_is_invalid(body):
    with pytest.raises(AppointmentsUnavailable) as caught:
        map_answer(answer(body))
    assert caught.value.code == "invalid_response"


def test_an_oversized_or_non_json_body_is_invalid():
    for response in (HttpResponse(200, b"x" * MAX_BODY_BYTES), HttpResponse(200, b"not json")):
        with pytest.raises(AppointmentsUnavailable) as caught:
            map_answer(response)
        assert caught.value.code == "invalid_response"


def test_the_client_sends_the_window_and_the_key():
    seen = {}

    def transport(method, url, headers, body, timeout):
        seen.update(method=method, url=url, headers=headers, timeout=timeout)
        return answer({"appointments": [], "truncated": False})

    client = AppointmentListClient("http://svc:8080/", "k", transport=transport)
    client.list("P 1/x", datetime(2026, 9, 26, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC))
    assert seen["method"] == "GET"
    assert seen["url"] == ("http://svc:8080/api/v1/patients/P%201%2Fx/appointments"
                           "?from=2026-09-26T00%3A00%3A00%2B00%3A00&to=2026-10-26T00%3A00%3A00%2B00%3A00")
    assert seen["headers"]["X-API-Key"] == "k" and seen["timeout"] == 5.0


def test_no_answer_is_unavailable_and_names_no_host():
    def transport(*_):
        raise OSError("connect to svc:8080 refused")

    with pytest.raises(AppointmentsUnavailable) as caught:
        AppointmentListClient("http://svc:8080", "k", transport=transport).list(
            "P-1", datetime(2026, 9, 26, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC))
    assert caught.value.code == "no_answer" and caught.value.__cause__ is None
    assert "svc" not in repr(AppointmentListClient("http://svc:8080", "secret-k"))


@pytest.mark.parametrize("env, built", [
    ({}, False),
    ({"APPOINTMENT_SERVICE_URL": "http://svc:8080"}, False),
    ({"APPOINTMENT_SERVICE_URL": "ftp://svc", "APPOINTMENT_API_KEY": "k"}, False),
    ({"APPOINTMENT_SERVICE_URL": "http://svc:8080", "APPOINTMENT_API_KEY": "k"}, True),
])
def test_build_list_client_needs_both_variables(env, built):
    assert (build_list_client(env) is not None) is built
