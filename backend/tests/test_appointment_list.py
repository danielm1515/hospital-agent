"""Sub-project 16 (design D4, D7): the appointment list client - the contract or nothing."""
import http.client as http_client
import json
import traceback
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
PATIENT_ID = ONE["patient_id"]


def answer(body, status=200):
    return HttpResponse(status, json.dumps(body).encode())


def test_a_valid_answer_is_parsed():
    result = map_answer(answer({"appointments": [ONE, {**ONE, "appointment_id": "APT-2", "status": "Cancelled",
                                                      "doctor_name": None, "location": None,
                                                      "required_documents": []}], "truncated": False}), PATIENT_ID)
    first, second = result.appointments
    assert first.appointment_at == datetime(2026, 10, 3, 7, 30, tzinfo=UTC)
    assert first.required_documents == ("CBC", "ECG")
    assert (second.status, second.doctor_name, second.location) == ("Cancelled", None, None)
    assert result.truncated is False


def test_patient_not_found_is_its_own_answer():
    with pytest.raises(PatientNotFound):
        map_answer(answer({"error": "patient_not_found"}, status=404), PATIENT_ID)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503, 504])
def test_any_other_status_is_unavailable(status):
    with pytest.raises(AppointmentsUnavailable) as caught:
        map_answer(answer({"error": "x"}, status=status), PATIENT_ID)
    assert caught.value.code == ("invalid_response" if status == 404 else f"status_{status}")


@pytest.mark.parametrize("body", [
    [],                                                   # not a dict
    {"appointments": "x", "truncated": False},            # not a list
    {"appointments": {}, "truncated": False},             # a dict is not a list either
    {"appointments": [ONE]},                              # no truncated
    {"appointments": [ONE], "truncated": "no"},           # truncated not a bool
    {"appointments": [1], "truncated": False},            # an item that is not a dict
    {"appointments": [{**ONE, "appointment_at": "2026-10-03T10:30:00"}], "truncated": False},  # naive
    {"appointments": [{**ONE, "appointment_at": "soon"}], "truncated": False},
    {"appointments": [{**ONE, "appointment_at": 123}], "truncated": False},          # not a string at all
    {"appointments": [{k: v for k, v in ONE.items() if k != "appointment_at"}], "truncated": False},  # missing
    {"appointments": [{**ONE, "status": "Moved"}], "truncated": False},
    {"appointments": [{**ONE, "status": ["Scheduled"]}], "truncated": False},        # unhashable, not a string
    {"appointments": [{**ONE, "appointment_id": ""}], "truncated": False},
    {"appointments": [{**ONE, "department": 7}], "truncated": False},
    {"appointments": [{**ONE, "doctor_name": 7}], "truncated": False},
    {"appointments": [{**ONE, "location": 7}], "truncated": False},
    {"appointments": [{**ONE, "required_documents": ["CBC", ""]}], "truncated": False},
    {"appointments": [{**ONE, "required_documents": "CBC"}], "truncated": False},
    {"appointments": [{**ONE, "patient_id": "P-99999"}], "truncated": False},        # another patient's row
])
def test_anything_but_the_contract_is_invalid(body):
    with pytest.raises(AppointmentsUnavailable) as caught:
        map_answer(answer(body), PATIENT_ID)
    assert caught.value.code == "invalid_response"


def test_an_oversized_or_non_json_body_is_invalid():
    for response in (HttpResponse(200, b"x" * MAX_BODY_BYTES), HttpResponse(200, b"not json")):
        with pytest.raises(AppointmentsUnavailable) as caught:
            map_answer(response, PATIENT_ID)
        assert caught.value.code == "invalid_response"


def test_deeply_nested_json_is_invalid():
    nested = b"[" * 20000 + b"]" * 20000
    with pytest.raises(AppointmentsUnavailable) as caught:
        map_answer(HttpResponse(200, nested), PATIENT_ID)
    assert caught.value.code == "invalid_response"


def test_size_bound_is_exact():
    small_body = json.dumps({"appointments": [], "truncated": False}).encode()
    accepted = small_body + b" " * (MAX_BODY_BYTES - 1 - len(small_body))
    assert len(accepted) == MAX_BODY_BYTES - 1
    assert map_answer(HttpResponse(200, accepted), PATIENT_ID).truncated is False

    rejected = small_body + b" " * (MAX_BODY_BYTES - len(small_body))
    assert len(rejected) == MAX_BODY_BYTES
    with pytest.raises(AppointmentsUnavailable) as caught:
        map_answer(HttpResponse(200, rejected), PATIENT_ID)
    assert caught.value.code == "invalid_response"


def test_the_client_sends_the_window_and_the_key():
    seen = {}

    def transport(method, url, headers, body, timeout):
        seen.update(method=method, url=url, headers=headers, timeout=timeout)
        return answer({"appointments": [], "truncated": False})

    client = AppointmentListClient("http://svc:8080/", "k", transport=transport)
    client.list(PATIENT_ID, datetime(2026, 9, 26, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC))
    assert seen["method"] == "GET"
    assert seen["url"] == (f"http://svc:8080/api/v1/patients/{PATIENT_ID}/appointments"
                           "?from=2026-09-26T00%3A00%3A00%2B00%3A00&to=2026-10-26T00%3A00%3A00%2B00%3A00")
    assert seen["headers"]["X-API-Key"] == "k" and seen["timeout"] == 5.0


def test_no_answer_is_unavailable_and_names_no_host():
    def transport(*_):
        raise OSError("connect to svc:8080 refused")

    client = AppointmentListClient("http://svc:8080", "k", transport=transport)
    with pytest.raises(AppointmentsUnavailable) as caught:
        client.list("P-1", datetime(2026, 9, 26, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC))
    assert caught.value.code == "no_answer"
    # __cause__ is None either way (implicit chaining sets __context__, not __cause__) - the real
    # marker that `from None` fired is __suppress_context__, and the host must not survive into
    # the exception's own traceback (which would carry the original OSError as __context__). The
    # client is built on its own line above so the "svc" literal in *this test's own source* is
    # never on a frame that is still on the stack when AppointmentsUnavailable is raised.
    assert caught.value.__suppress_context__ is True
    assert "svc" not in "".join(traceback.format_exception(caught.value))
    assert "svc" not in repr(AppointmentListClient("http://svc:8080", "secret-k"))


def test_a_transport_http_exception_is_also_no_answer():
    def transport(*_):
        raise http_client.HTTPException("bad")

    with pytest.raises(AppointmentsUnavailable) as caught:
        AppointmentListClient("http://svc:8080", "k", transport=transport).list(
            "P-1", datetime(2026, 9, 26, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC))
    assert caught.value.code == "no_answer"


def test_list_rejects_naive_datetimes():
    client = AppointmentListClient("http://svc:8080", "k", transport=lambda *a: None)
    with pytest.raises(ValueError):
        client.list("P-1", datetime(2026, 9, 26), datetime(2026, 10, 26, tzinfo=UTC))
    with pytest.raises(ValueError):
        client.list("P-1", datetime(2026, 9, 26, tzinfo=UTC), datetime(2026, 10, 26))


def test_list_rejects_a_malformed_patient_id():
    client = AppointmentListClient("http://svc:8080", "k", transport=lambda *a: None)
    for bad_id in ("P 1/x", "..", "", "-leading-dash", "x" * 65):
        with pytest.raises(ValueError):
            client.list(bad_id, datetime(2026, 9, 26, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC))


@pytest.mark.parametrize("env, built", [
    ({}, False),
    ({"APPOINTMENT_SERVICE_URL": "http://svc:8080"}, False),
    ({"APPOINTMENT_SERVICE_URL": "ftp://svc", "APPOINTMENT_API_KEY": "k"}, False),
    ({"APPOINTMENT_SERVICE_URL": "http://svc:8080", "APPOINTMENT_API_KEY": "k"}, True),
])
def test_build_list_client_needs_both_variables(env, built):
    assert (build_list_client(env) is not None) is built
