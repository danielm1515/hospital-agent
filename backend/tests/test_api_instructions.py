"""Sub-project 18 (design D12, D14): the patient's and the staff's instruction-text routes."""
import logging

import pytest
from fastapi.testclient import TestClient

from hospital_agent.api.app import create_app
from hospital_agent.auth import demo_password
from hospital_agent.instruction_client import (
    Instruction,
    InstructionClient,
    InstructionNotFound,
    InstructionUnavailable,
)

PATIENT, NURSE, ADMIN = "P-10041", "coordinator_nurse", "admin_coordinator"
# Real, always-current entries in policy/data/approved_instruction_sources.json (valid
# 2026-01-01..2030-01-01) - no monkeypatching needed for the "approved" path.
APPROVED_SOURCE, APPROVED_VERSION = "INSTR-PREP-COLONOSCOPY", "3"
# Also real: an expired entry (valid_until 2025-12-31) and a not-yet-valid one (valid_from 2090).
EXPIRED_SOURCE, EXPIRED_VERSION = "INSTR-RETIRED-2025", "1"
FUTURE_SOURCE, FUTURE_VERSION = "INSTR-DRAFT-2090", "1"

GOOD = Instruction(APPROVED_SOURCE, APPROVED_VERSION, "לפני הבדיקה", "יש לצום 12 שעות מראש.")


class FakeInstructions:
    def __init__(self, result=None, raises=None):
        self.result = result or GOOD
        self.raises = raises
        self.calls = []

    def get(self, source_id, version):
        self.calls.append((source_id, version))
        if self.raises:
            raise self.raises
        return self.result


def make(app_engine, fake):
    return TestClient(create_app(app_engine, instructions_client=fake))


def auth(client, user_id):
    token = client.post("/api/auth/login", json={"user_id": user_id, "password": demo_password()}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


# --- the patient route -------------------------------------------------------------------

def test_an_approved_source_gives_200(app_engine):
    fake = FakeInstructions()
    with make(app_engine, fake) as client:
        response = client.get(f"/api/patient/instructions/{APPROVED_SOURCE}",
                              params={"version": APPROVED_VERSION}, headers=auth(client, PATIENT))
    assert response.status_code == 200
    assert response.json() == {"source_id": APPROVED_SOURCE, "version": APPROVED_VERSION,
                               "title": GOOD.title, "text": GOOD.text}
    assert fake.calls == [(APPROVED_SOURCE, APPROVED_VERSION)]


def test_an_unlisted_source_is_404_and_the_service_is_never_called(app_engine):
    fake = FakeInstructions()
    with make(app_engine, fake) as client:
        response = client.get("/api/patient/instructions/INSTR-NO-SUCH-THING",
                              params={"version": "1"}, headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (404, "instruction_not_approved")
    assert fake.calls == []


def test_an_expired_source_is_404_and_the_service_is_never_called(app_engine):
    fake = FakeInstructions()
    with make(app_engine, fake) as client:
        response = client.get(f"/api/patient/instructions/{EXPIRED_SOURCE}",
                              params={"version": EXPIRED_VERSION}, headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (404, "instruction_not_approved")
    assert fake.calls == []


def test_a_not_yet_valid_source_is_404_and_the_service_is_never_called(app_engine):
    fake = FakeInstructions()
    with make(app_engine, fake) as client:
        response = client.get(f"/api/patient/instructions/{FUTURE_SOURCE}",
                              params={"version": FUTURE_VERSION}, headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (404, "instruction_not_approved")
    assert fake.calls == []


def test_a_wrong_version_for_an_approved_source_is_404(app_engine):
    fake = FakeInstructions()
    with make(app_engine, fake) as client:
        response = client.get(f"/api/patient/instructions/{APPROVED_SOURCE}",
                              params={"version": "999"}, headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (404, "instruction_not_approved")
    assert fake.calls == []


@pytest.mark.parametrize("source_id, version", [
    ("bad!id", "1"),                  # source_id not the id shape
    (APPROVED_SOURCE, "bad!ver"),      # version not the id shape
    (APPROVED_SOURCE, ""),             # empty version (as a real query value, see below)
])
def test_a_bad_id_or_version_is_422(app_engine, source_id, version):
    fake = FakeInstructions()
    with make(app_engine, fake) as client:
        response = client.get(f"/api/patient/instructions/{source_id}", params={"version": version},
                              headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (422, "invalid_instruction")
    assert fake.calls == []


def test_a_missing_version_is_422(app_engine):
    fake = FakeInstructions()
    with make(app_engine, fake) as client:
        response = client.get(f"/api/patient/instructions/{APPROVED_SOURCE}", headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (422, "invalid_instruction")
    assert fake.calls == []


def test_not_configured_is_404_not_enabled_even_for_a_bad_id(app_engine):
    """Mirrors appointments.read()'s own convention: "not enabled" wins over a query that would
    otherwise be a 422 or a 404 instruction_not_approved."""
    with make(app_engine, None) as client:
        response = client.get("/api/patient/instructions/bad!id", params={"version": "not!valid!either"},
                              headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (404, "instructions_not_enabled")


@pytest.mark.parametrize("raises, code", [
    (InstructionUnavailable("no_answer"), "instructions_unavailable"),
    (InstructionUnavailable("status_500"), "instructions_unavailable"),
    (InstructionUnavailable("invalid_response"), "instructions_unavailable"),
])
def test_each_client_failure_is_503(app_engine, raises, code):
    with make(app_engine, FakeInstructions(raises=raises)) as client:
        response = client.get(f"/api/patient/instructions/{APPROVED_SOURCE}",
                              params={"version": APPROVED_VERSION}, headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (503, "instructions_unavailable")


def test_the_services_own_404_is_503_not_a_pass_through(app_engine):
    """The registry approved this source_id+version, but the service disagrees (its own 404) -
    the registry and the service disagreeing must never be told apart from any other failure,
    and never silently treated as approved-and-fine."""
    with make(app_engine, FakeInstructions(raises=InstructionNotFound())) as client:
        response = client.get(f"/api/patient/instructions/{APPROVED_SOURCE}",
                              params={"version": APPROVED_VERSION}, headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (503, "instructions_unavailable")


def test_a_mismatched_answer_is_unavailable_not_the_wrong_text(app_engine):
    """map_answer() itself already refuses an answer for a different source/version (tested in
    test_instruction_client.py); this checks the route surfaces that as the same 503, never the
    mismatched Instruction."""
    mismatched = InstructionUnavailable("invalid_response")
    with make(app_engine, FakeInstructions(raises=mismatched)) as client:
        response = client.get(f"/api/patient/instructions/{APPROVED_SOURCE}",
                              params={"version": APPROVED_VERSION}, headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (503, "instructions_unavailable")


def test_a_key_with_an_inner_newline_is_503_not_500(app_engine, caplog):
    """Fix round 1 (I2), copying test_api_appointments.py's own test_a_bad_patient_id_is_503_not_500:
    a *real* InstructionClient (not a Fake) whose API key carries an embedded newline - the
    transport's own header validation raises a plain ValueError, which InstructionClient.get()
    does not catch (test_instruction_client.py). instructions.read() must catch it, never
    surface as a 500, and log the outcome (`client_error`) without the key or the source_id."""
    real_client = InstructionClient("http://127.0.0.1:1", "bad\nkey")
    with caplog.at_level(logging.DEBUG, logger="hospital_agent.api.instructions"):
        with make(app_engine, real_client) as client:
            response = client.get(f"/api/patient/instructions/{APPROVED_SOURCE}",
                                  params={"version": APPROVED_VERSION}, headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (503, "instructions_unavailable")
    assert "instruction read: client_error in" in caplog.text
    # Task 8: "bad\nkey" itself could never appear in a one-line log record; the key's own
    # characters must not appear at all.
    assert "bad" not in caplog.text and APPROVED_SOURCE not in caplog.text


def test_the_patient_route_is_patients_only(app_engine):
    with make(app_engine, FakeInstructions()) as client:
        response = client.get(f"/api/patient/instructions/{APPROVED_SOURCE}", params={"version": APPROVED_VERSION},
                              headers=auth(client, NURSE))
        anonymous = client.get(f"/api/patient/instructions/{APPROVED_SOURCE}", params={"version": APPROVED_VERSION})
    assert (response.status_code, response.json()["detail"]) == (403, "patients_only")
    assert anonymous.status_code == 401


def test_the_log_never_names_the_source_or_the_text(app_engine, caplog):
    with caplog.at_level(logging.DEBUG, logger="hospital_agent.api.instructions"):
        with make(app_engine, FakeInstructions()) as client:
            client.get(f"/api/patient/instructions/{APPROVED_SOURCE}", params={"version": APPROVED_VERSION},
                      headers=auth(client, PATIENT))
    assert "instruction read: ok in" in caplog.text
    assert APPROVED_SOURCE not in caplog.text
    assert GOOD.title not in caplog.text and GOOD.text not in caplog.text


def test_the_log_never_names_the_source_on_failure(app_engine, caplog):
    with caplog.at_level(logging.DEBUG, logger="hospital_agent.api.instructions"):
        with make(app_engine, FakeInstructions(raises=InstructionUnavailable("no_answer"))) as client:
            client.get(f"/api/patient/instructions/{APPROVED_SOURCE}", params={"version": APPROVED_VERSION},
                      headers=auth(client, PATIENT))
    assert "instruction read: no_answer in" in caplog.text
    assert APPROVED_SOURCE not in caplog.text


# --- OPA unavailable is not "not approved" (Task 8, carried Task 6 Minor) -----------------

@pytest.mark.parametrize("route", ["patient", "staff"])
def test_opa_binary_missing_is_503_unavailable_and_the_service_is_never_called(app_engine, caplog,
                                                                               monkeypatch, tmp_path, route):
    """The real OPA binary cannot be found (an empty PATH): the registry cannot be asked, so the
    answer is 503 instructions_unavailable - never 404 instruction_not_approved, which would tell
    the reader the text is unapproved - and still closed: the appointment-service is never
    asked. One code-only log line, `policy_unavailable`."""
    fake = FakeInstructions()
    user = PATIENT if route == "patient" else NURSE
    with caplog.at_level(logging.DEBUG, logger="hospital_agent.api.instructions"):
        with make(app_engine, fake) as client:
            headers = auth(client, user)
            monkeypatch.setenv("PATH", str(tmp_path))  # after login: only OPA goes missing
            response = client.get(f"/api/{route}/instructions/{APPROVED_SOURCE}",
                                  params={"version": APPROVED_VERSION}, headers=headers)
    assert (response.status_code, response.json()["detail"]) == (503, "instructions_unavailable")
    assert fake.calls == []
    assert "instruction read: policy_unavailable in" in caplog.text
    assert APPROVED_SOURCE not in caplog.text


def test_a_real_deny_is_still_404_and_not_logged_as_unavailable(app_engine, caplog):
    with caplog.at_level(logging.DEBUG, logger="hospital_agent.api.instructions"):
        with make(app_engine, FakeInstructions()) as client:
            response = client.get(f"/api/patient/instructions/{EXPIRED_SOURCE}",
                                  params={"version": EXPIRED_VERSION}, headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (404, "instruction_not_approved")
    assert "policy_unavailable" not in caplog.text


# --- the staff route ----------------------------------------------------------------------

@pytest.mark.parametrize("staff", [NURSE, ADMIN])
def test_staff_read_an_approved_source(app_engine, staff):
    fake = FakeInstructions()
    with make(app_engine, fake) as client:
        response = client.get(f"/api/staff/instructions/{APPROVED_SOURCE}", params={"version": APPROVED_VERSION},
                              headers=auth(client, staff))
    assert response.status_code == 200
    assert fake.calls == [(APPROVED_SOURCE, APPROVED_VERSION)]


def test_staff_route_is_staff_only(app_engine):
    with make(app_engine, FakeInstructions()) as client:
        response = client.get(f"/api/staff/instructions/{APPROVED_SOURCE}", params={"version": APPROVED_VERSION},
                              headers=auth(client, PATIENT))
    assert (response.status_code, response.json()["detail"]) == (403, "staff_only")


def test_staff_route_not_configured_is_404_not_enabled(app_engine):
    with make(app_engine, None) as client:
        response = client.get(f"/api/staff/instructions/{APPROVED_SOURCE}", params={"version": APPROVED_VERSION},
                              headers=auth(client, NURSE))
    assert (response.status_code, response.json()["detail"]) == (404, "instructions_not_enabled")


def test_staff_route_unapproved_source_is_404_and_the_service_is_never_called(app_engine):
    fake = FakeInstructions()
    with make(app_engine, fake) as client:
        response = client.get("/api/staff/instructions/INSTR-NO-SUCH-THING", params={"version": "1"},
                              headers=auth(client, NURSE))
    assert (response.status_code, response.json()["detail"]) == (404, "instruction_not_approved")
    assert fake.calls == []
