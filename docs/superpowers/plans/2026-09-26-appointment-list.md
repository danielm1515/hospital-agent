# The patient's appointment list — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The patient sees their appointments on "הפניות שלי", and a staff member sees the case's patient's appointments on the review screen, both filterable by a from-to day range (default today + 30 days).

**Architecture:** The appointment-service (the owner of the fact) gets a JSON list endpoint beside CheckAppointment. hospital-agent reads it with a small client of its own (a recorded exception, `spec_corrections` row 89 — read-only, outside the FSM), exposes one patient route and one staff route, and the React UI shows one shared panel on both screens.

**Tech Stack:** appointment-service: FastAPI + SQLAlchemy on SQLite, pytest. hospital-agent: FastAPI, standard-library HTTP (`execution/http.py`), pytest in Docker; React 18 + Vite + Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-26-appointment-list-design.md` (hospital-agent worktree).

## Global Constraints

- Two worktrees; never edit or run anything in the owner's trees `C:/Apps/פרויקט גמר AI/hospital-agent` or `C:/Apps/פרויקט גמר AI/appointment-service` (the running stacks use them):
  - appointment-service: `C:/Apps/פרויקט גמר AI/appointment-service-list` (branch `feature/list-appointments`). Tests: `MSYS_NO_PATHCONV=1 docker run --rm -v "C:/Apps/פרויקט גמר AI/appointment-service-list:/src" appt-test python -m pytest -q -p no:cacheprovider` (image `appt-test` already built; baseline 78 passed).
  - hospital-agent: `C:/Apps/פרויקט גמר AI/hospital-agent-appointments` (branch `feature/appointment-list`). Backend tests ONLY via `cd "C:/Apps/פרויקט גמר AI/hospital-agent-appointments" && export MSYS_NO_PATHCONV=1 && docker compose -p appts-proto -f docker-compose.yml -f C:/Users/DANIEL~1.MAM/AppData/Local/Temp/claude/C--Apps------------AI/4ba1785e-5a6d-42d4-88ab-e01babb23dab/scratchpad/compose.proto.yml run --rm backend pytest <args>` — never plain `docker compose` (`.env` points at a live AWS RDS). Foreground, one test process at a time. Frontend: `cd frontend && npm ci` once, then `npm test` / `npm run build`.
- Never read or print `.env` or any key. Never push. Never `git stash` (shared stack) — RED checks by a temporary edit.
- Commits: imperative, repo style, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Range rule (design D2/D5): `from` inclusive, `to` exclusive, timezone-aware, `from < to`, at most 366 days. Defaults (hospital-agent only): `from` = now, `to` = `from` + 30 days.
- Codes (design D6): `404 appointments_not_enabled`, `404 case_not_found`, `404 patient_not_found`, `503 appointments_unavailable`, `422 invalid_range`. Service-side `400 validation_error`.
- No `patient_id` and no appointment in hospital-agent's application log (§12.3) — only an outcome code and latency.
- Patient UI never shows a code; staff UI shows the Hebrew label beside the code (CLAUDE.md frontend rules). Logical CSS only, new selectors only, tokens from `tokens.css`, no thick one-sided colored borders.

---

### Task 1: appointment-service — `GET /api/v1/patients/{patient_id}/appointments`

**Files:**
- Modify: `app/main.py` (new helper `_registry_refusal`, used by CheckAppointment and the new route; the new route after `check_appointment`)
- Modify: `app/schemas.py` (`AppointmentList`)
- Create: `tests/test_list_api.py`
- Modify: `README.md` (a short section beside the CheckAppointment curl example)

**Interfaces:**
- Produces: `GET /api/v1/patients/{patient_id}/appointments?from=<iso>&to=<iso>` → `200 {"appointments": [AppointmentOut…], "truncated": bool}`; `400 validation_error`, `401 unauthorized`, `404 patient_not_found`, `503 patient_registry_unavailable`, `504 timeout` (P-TIMEOUT simulation), `503 service_unavailable`. Audit row `operation="ListAppointments"`, `result` in `found` / `not_found` (empty list) / `patient_not_found` / `technical_failure`. `MAX_LIST = 100`, `MAX_LIST_WINDOW = timedelta(days=366)`.

- [ ] **Step 1: Write the failing tests** — `tests/test_list_api.py`:

```python
"""ListAppointments (hospital-agent sub-project 16, design D1-D3): the patient's appointments in a
window, both statuses, ordered, capped, with the same key and registry checks as CheckAppointment."""
import os

os.environ["ENABLE_FAILURE_SIMULATION"] = "true"
os.environ["MOCK_TIMEOUT_PATIENT_ID"] = "P-TIMEOUT"
os.environ["API_AUTH_ENABLED"] = "true"
os.environ["APPOINTMENT_API_KEY"] = "test-key"

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import Appointment, AuditLog
from app.patient_registry import RegistryUnavailable

IL = ZoneInfo("Asia/Jerusalem")
KEY = {"X-API-Key": "test-key"}
URL = "/api/v1/patients/{}/appointments"


class FakeRegistry:
    def __init__(self, known=("P-10041", "P-20000", "P-TIMEOUT"), down=False):
        self.known, self.down = set(known), down

    def exists(self, patient_id):
        if self.down:
            raise RegistryUnavailable
        return patient_id in self.known


def make(tmp_path, registry=None):
    app = create_app(f"sqlite:///{(tmp_path / 't.db').as_posix()}", seed_demo_data=True,
                     patient_registry=registry)
    return app, TestClient(app)


def add(app, appointment_id, at, status="Scheduled", patient_id="P-10041"):
    with app.state.SessionLocal() as s:
        s.add(Appointment(appointment_id=appointment_id, patient_id=patient_id, department="Neurology",
                          appointment_at=at, status=status))
        s.commit()


def window(start, end):
    return {"from": start.isoformat(), "to": end.isoformat()}


def test_lists_both_statuses_in_order_inside_the_window(tmp_path):
    app, client = make(tmp_path)
    with client:
        add(app, "APT-C", datetime(2026, 10, 3, 12, 0, tzinfo=IL), status="Cancelled")
        add(app, "APT-W", datetime(2026, 12, 1, 9, 0, tzinfo=IL))  # winter time, +02:00
        add(app, "APT-OUT", datetime(2027, 3, 1, 9, 0, tzinfo=IL))
        response = client.get(URL.format("P-10041"), headers=KEY,
                              params=window(datetime(2026, 10, 1, tzinfo=IL), datetime(2027, 1, 1, tzinfo=IL)))
    assert response.status_code == 200
    body = response.json()
    assert [a["appointment_id"] for a in body["appointments"]] == ["APT-8391", "APT-C", "APT-W"]
    assert [a["status"] for a in body["appointments"]] == ["Scheduled", "Cancelled", "Scheduled"]
    assert body["appointments"][2]["appointment_at"] == "2026-12-01T09:00:00+02:00"
    assert body["appointments"][0]["required_documents"] == ["CBC", "COAGULATION_TESTS", "ECG"]
    assert body["truncated"] is False


def test_from_is_inclusive_and_to_is_exclusive(tmp_path):
    app, client = make(tmp_path)
    at = datetime(2026, 10, 3, 10, 30, tzinfo=IL)  # the seeded APT-8391
    with client:
        up_to = client.get(URL.format("P-10041"), headers=KEY, params=window(at - timedelta(days=1), at)).json()
        from_ = client.get(URL.format("P-10041"), headers=KEY, params=window(at, at + timedelta(minutes=1))).json()
    assert up_to["appointments"] == []
    assert [a["appointment_id"] for a in from_["appointments"]] == ["APT-8391"]


def test_another_patients_appointments_are_never_listed(tmp_path):
    app, client = make(tmp_path)
    with client:
        body = client.get(URL.format("P-20000"), headers=KEY,
                          params=window(datetime(2026, 9, 1, tzinfo=IL), datetime(2027, 9, 1, tzinfo=IL))).json()
    assert [a["patient_id"] for a in body["appointments"]] == ["P-20000"]


def test_the_list_is_capped_and_says_so(tmp_path):
    app, client = make(tmp_path)
    start = datetime(2027, 1, 1, 8, 0, tzinfo=IL)
    with client:
        for i in range(101):
            add(app, f"APT-{i:03d}", start + timedelta(hours=i))
        body = client.get(URL.format("P-10041"), headers=KEY,
                          params=window(start, start + timedelta(days=30))).json()
    assert len(body["appointments"]) == 100 and body["truncated"] is True
    assert body["appointments"][-1]["appointment_id"] == "APT-099"


def test_a_missing_or_wrong_key_is_401(tmp_path):
    _app, client = make(tmp_path)
    params = window(datetime(2026, 9, 1, tzinfo=IL), datetime(2026, 10, 1, tzinfo=IL))
    with client:
        missing = client.get(URL.format("P-10041"), params=params)
        wrong = client.get(URL.format("P-10041"), params=params, headers={"X-API-Key": "nope"})
    assert (missing.status_code, missing.json()["error"]) == (401, "unauthorized")
    assert wrong.status_code == 401


PARAMS = window(datetime(2026, 9, 1, tzinfo=IL), datetime(2026, 10, 1, tzinfo=IL))


def test_an_unknown_patient_is_404_like_check_appointment(tmp_path):
    _app, client = make(tmp_path, FakeRegistry(known=("P-20000",)))
    with client:
        unknown = client.get(URL.format("P-10041"), headers=KEY, params=PARAMS)
    assert (unknown.status_code, unknown.json()["error"]) == (404, "patient_not_found")


def test_an_unreachable_registry_is_503_like_check_appointment(tmp_path):
    _app, client = make(tmp_path, FakeRegistry(down=True))
    with client:
        down = client.get(URL.format("P-10041"), headers=KEY, params=PARAMS)
    assert (down.status_code, down.json()["error"]) == (503, "patient_registry_unavailable")


def test_the_timeout_simulation_applies(tmp_path):
    _app, client = make(tmp_path)
    with client:
        response = client.get(URL.format("P-TIMEOUT"), headers=KEY,
                              params=window(datetime(2026, 9, 1, tzinfo=IL), datetime(2026, 10, 1, tzinfo=IL)))
    assert (response.status_code, response.json()["error"]) == (504, "timeout")


def test_a_bad_window_is_400(tmp_path):
    _app, client = make(tmp_path)
    good = datetime(2026, 9, 1, tzinfo=IL)
    cases = [
        {"from": "2026-09-01T00:00:00", "to": "2026-10-01T00:00:00+03:00"},  # naive from
        window(good, good),                                                   # empty
        window(good + timedelta(days=1), good),                               # reversed
        window(good, good + timedelta(days=367)),                             # over 366 days
        {"from": "yesterday", "to": "2026-10-01T00:00:00+03:00"},             # not a date
        {"to": "2026-10-01T00:00:00+03:00"},                                  # missing from
    ]
    with client:
        for params in cases:
            response = client.get(URL.format("P-10041"), headers=KEY, params=params)
            assert (response.status_code, response.json()["error"]) == (400, "validation_error"), params


def test_each_call_writes_a_list_audit_row(tmp_path):
    app, client = make(tmp_path)
    with client:
        client.get(URL.format("P-10041"), headers=KEY,
                   params=window(datetime(2026, 9, 1, tzinfo=IL), datetime(2026, 11, 1, tzinfo=IL)))
        client.get(URL.format("P-10041"), headers=KEY,
                   params=window(datetime(2020, 1, 1, tzinfo=IL), datetime(2020, 2, 1, tzinfo=IL)))
        with app.state.SessionLocal() as s:
            rows = s.scalars(select(AuditLog).where(AuditLog.operation == "ListAppointments")
                             .order_by(AuditLog.timestamp)).all()
    assert [r.result for r in rows] == ["found", "not_found"]
```

(Settings are read from the environment when `create_app` runs; if `API_AUTH_ENABLED` / `APPOINTMENT_API_KEY` are read at import time instead, set them the way `tests/test_api.py` / `conftest.py` already do for the auth tests. Check how `create_app` takes the registry — `patient_registry=` per `test_patient_registry.py:33`.)

- [ ] **Step 2: Run to see them fail** — expected: 404 on the new path (route missing).

- [ ] **Step 3: Implement.** In `app/schemas.py`:

```python
class AppointmentList(BaseModel):
    appointments: list[AppointmentOut]
    truncated: bool
```

In `app/main.py`, module level near the other constants:

```python
ISRAEL = ZoneInfo("Asia/Jerusalem")
MAX_LIST = 100
MAX_LIST_WINDOW = timedelta(days=366)
```

Extract the registry block of `check_appointment` into a helper and call it from both routes (CheckAppointment's behaviour and audit rows stay byte-for-byte the same; its existing tests prove it):

```python
def _registry_refusal(session, registry, *, patient_id, case_id, execution_id, started, operation):
    """The registry's verdict (design §4 of the registry design): None when the patient may be
    served, else the 404 / 503 answer - with its audit row already written."""
    if registry is None:
        return None
    try:
        known = registry.exists(patient_id)
    except RegistryUnavailable:
        logger.warning("patient registry unavailable")
        _write_audit(session, case_id=case_id, execution_id=execution_id, patient_id=patient_id,
                     result="technical_failure", operation=operation,
                     latency_ms=round((time.perf_counter() - started) * 1000))
        return JSONResponse(status_code=503,
                            content={"error": "patient_registry_unavailable",
                                     "message": "The patient registry could not be reached"},
                            headers={"X-Case-ID": case_id, "X-Execution-ID": execution_id})
    if not known:
        _write_audit(session, case_id=case_id, execution_id=execution_id, patient_id=patient_id,
                     result="patient_not_found", operation=operation,
                     latency_ms=round((time.perf_counter() - started) * 1000))
        return JSONResponse(status_code=404,
                            content={"error": "patient_not_found", "message": "The patient is not in the registry"},
                            headers={"X-Case-ID": case_id, "X-Execution-ID": execution_id})
    return None
```

The new route, after `check_appointment` (same auth block, same headers, same failure handling; the window check returns the same `400 validation_error` body shape the app's validation handler uses — read that handler at `main.py:240-247` and reuse its body):

```python
    @app.get(
        "/api/v1/patients/{patient_id}/appointments",
        response_model=AppointmentList,
        responses={400: {"model": ErrorResult}, 401: {"model": ErrorResult}, 404: {"model": ErrorResult},
                   503: {"model": ErrorResult}, 504: {"model": ErrorResult}},
        tags=["Appointments"],
        operation_id="ListAppointments",
    )
    def list_appointments(
        request: Request,
        response: Response,
        patient_id: str = ApiPath(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
        start: datetime = Query(alias="from"),
        end: datetime = Query(alias="to"),
        x_case_id: str | None = Header(default=None, alias="X-Case-ID"),
        x_execution_id: str | None = Header(default=None, alias="X-Execution-ID"),
        supplied_api_key: str | None = Security(api_key_header),
    ) -> AppointmentList | JSONResponse:
        """Every appointment of the patient (Scheduled and Cancelled) with from <= at < to, oldest
        first, at most MAX_LIST (then truncated). The rows hold Israel wall-clock time without an
        offset (AppointmentOut._israel_time), so the window is compared in that zone."""
        # (the same API-key block as check_appointment, verbatim)
        if start.tzinfo is None or end.tzinfo is None or not start < end or end - start > MAX_LIST_WINDOW:
            return JSONResponse(status_code=400, content={"error": "validation_error",
                                "message": "from and to must be timezone-aware, from < to, at most 366 days apart"})
        started = time.perf_counter()
        case_id = (x_case_id or str(uuid4()))[:128]
        execution_id = (x_execution_id or str(uuid4()))[:128]
        response.headers["X-Case-ID"] = case_id
        response.headers["X-Execution-ID"] = execution_id
        with request.app.state.SessionLocal() as session:
            try:
                cfg: Settings = request.app.state.settings
                if cfg.enable_failure_simulation and patient_id == cfg.mock_timeout_patient_id:
                    raise SimulatedTimeout
                refusal = _registry_refusal(session, request.app.state.patient_registry, patient_id=patient_id,
                                            case_id=case_id, execution_id=execution_id, started=started,
                                            operation="ListAppointments")
                if refusal is not None:
                    return refusal
                low = start.astimezone(ISRAEL).replace(tzinfo=None)
                high = end.astimezone(ISRAEL).replace(tzinfo=None)
                rows = session.scalars(
                    select(Appointment)
                    .where(Appointment.patient_id == patient_id,
                           Appointment.appointment_at >= low, Appointment.appointment_at < high)
                    .order_by(Appointment.appointment_at.asc(), Appointment.appointment_id.asc())
                    .limit(MAX_LIST + 1)
                ).all()
                _write_audit(session, case_id=case_id, execution_id=execution_id, patient_id=patient_id,
                             result="found" if rows else "not_found", operation="ListAppointments",
                             latency_ms=round((time.perf_counter() - started) * 1000))
                return AppointmentList(appointments=rows[:MAX_LIST], truncated=len(rows) > MAX_LIST)
            # (the same SimulatedTimeout / SQLAlchemyError handling as check_appointment, with
            #  operation="ListAppointments" on the timeout's audit row)
```

Where the placeholder comments say "the same … as check_appointment", copy that code — do not leave a comment. Keep the query on naive local values (validated by the prototype, DST included). Add the imports (`timedelta`, `ZoneInfo`, `Query`, `AppointmentList`) that are missing.

- [ ] **Step 4: Run the whole suite** — expected: 78 + the new tests, all pass.

- [ ] **Step 5: README** — under the CheckAppointment example, add a Hebrew section "רשימת התורים של מטופל" with a curl example (`from`/`to` ISO with offset, `X-API-Key`), the rules (inclusive/exclusive, up to 366 days, both statuses, 100 rows + `truncated`) and the error codes.

- [ ] **Step 6: Commit** — `git add app tests README.md && git commit -m "Add ListAppointments: a patient's appointments in a window" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`

---

### Task 2: hospital-agent — the list client (`appointment_list.py`)

**Files:**
- Create: `backend/hospital_agent/appointment_list.py`
- Create: `backend/tests/test_appointment_list.py`

**Interfaces:**
- Produces: `Appointment` (frozen dataclass: `appointment_id: str`, `appointment_at: datetime`, `department: str`, `doctor_name: str | None`, `location: str | None`, `status: str`, `required_documents: tuple[str, ...]`); `AppointmentList(appointments: tuple[Appointment, ...], truncated: bool)`; exceptions `AppointmentsUnavailable(code)` (`.code` in `no_answer`, `status_<N>`, `invalid_response`) and `PatientNotFound`; `map_answer(response: HttpResponse) -> AppointmentList`; `AppointmentListClient(base_url, api_key, *, transport=None, timeout=5.0)` with `.list(patient_id: str, start: datetime, end: datetime) -> AppointmentList`; `build_list_client(env=None) -> AppointmentListClient | None`; `STATUSES = frozenset({"Scheduled", "Cancelled"})`.

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_appointment_list.py`:

```python
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
```

- [ ] **Step 2: Run to see them fail** — `pytest tests/test_appointment_list.py -v` → ImportError.

- [ ] **Step 3: Implement** `backend/hospital_agent/appointment_list.py`:

```python
"""Sub-project 16 (design docs/superpowers/specs/2026-09-26-appointment-list-design.md, D4-D7): the
patient's appointments, read straight from the appointment-service for the patient's and the
staff's screens.

Not a plan step: nothing is proposed, allowed by policy, retried, turned into an event or stored -
the second recorded exception to "only the Tool Executor calls an external system"
(docs/spec_corrections.md row 89, beside row 79). Anything that is not the contract - no answer, any
status but 200 (or the registry's 404), a body that is not exactly the documented shape - is
AppointmentsUnavailable, never a partial list. Standard library only, over the shared transport in
execution.http (no redirect, no proxy, a bounded body). The URL and the key never reach a repr, an
exception or a log line.
"""
from __future__ import annotations

import http.client as http_client
import json
import os
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from .execution import http
from .execution.http import MAX_BODY_BYTES, HttpResponse

TIMEOUT_SECONDS = 5.0  # the same bound as CheckAppointment (sub-project 10 design §2.4)
STATUSES = frozenset({"Scheduled", "Cancelled"})

Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], HttpResponse]


class AppointmentsUnavailable(Exception):
    """The list could not be read. `code` is short and carries no URL, key or patient data."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PatientNotFound(Exception):
    """The appointment-service's registry does not know the patient (its 404 patient_not_found)."""


@dataclass(frozen=True)
class Appointment:
    appointment_id: str
    appointment_at: datetime
    department: str
    doctor_name: str | None
    location: str | None
    status: str
    required_documents: tuple[str, ...]


@dataclass(frozen=True)
class AppointmentList:
    appointments: tuple[Appointment, ...]
    truncated: bool


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _optional_text(value: object) -> bool:
    return value is None or isinstance(value, str)


def _appointment(item: object) -> Appointment:
    if not isinstance(item, dict):
        raise AppointmentsUnavailable("invalid_response")
    at_text, documents = item.get("appointment_at"), item.get("required_documents")
    if not (_text(item.get("appointment_id")) and _text(item.get("department")) and _text(at_text)
            and _optional_text(item.get("doctor_name")) and _optional_text(item.get("location"))
            and item.get("status") in STATUSES
            and isinstance(documents, list) and all(_text(d) for d in documents)):
        raise AppointmentsUnavailable("invalid_response")
    try:
        at = datetime.fromisoformat(at_text)
    except ValueError:
        raise AppointmentsUnavailable("invalid_response") from None
    if at.tzinfo is None:
        raise AppointmentsUnavailable("invalid_response")
    return Appointment(item["appointment_id"], at, item["department"], item.get("doctor_name"),
                       item.get("location"), item["status"], tuple(sorted(set(documents))))


def map_answer(response: HttpResponse) -> AppointmentList:
    if len(response.body) >= MAX_BODY_BYTES:
        raise AppointmentsUnavailable("invalid_response")
    try:
        body = json.loads(response.body)
    except ValueError:
        body = None
    if response.status == 404:
        if isinstance(body, dict) and body.get("error") == "patient_not_found":
            raise PatientNotFound
        raise AppointmentsUnavailable("invalid_response")
    if response.status != 200:
        raise AppointmentsUnavailable(f"status_{response.status}")
    if not (isinstance(body, dict) and isinstance(body.get("appointments"), list)
            and isinstance(body.get("truncated"), bool)):
        raise AppointmentsUnavailable("invalid_response")
    return AppointmentList(tuple(_appointment(item) for item in body["appointments"]), body["truncated"])


def _urllib_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None,
                      timeout: float) -> HttpResponse:
    return http.request(method, url, headers, body, timeout)


class AppointmentListClient:
    def __init__(self, base_url: str, api_key: str, *, transport: Transport | None = None,
                 timeout: float = TIMEOUT_SECONDS) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport or _urllib_transport
        self.timeout = timeout

    def __repr__(self) -> str:  # the URL and the key stay out of every log and traceback
        return "AppointmentListClient()"

    def list(self, patient_id: str, start: datetime, end: datetime) -> AppointmentList:
        query = urllib.parse.urlencode({"from": start.isoformat(), "to": end.isoformat()})
        url = f"{self._base_url}/api/v1/patients/{urllib.parse.quote(patient_id, safe='')}/appointments?{query}"
        headers = {"X-API-Key": self._api_key, "Accept": "application/json"}
        try:
            response = self._transport("GET", url, headers, None, self.timeout)
        except (OSError, http_client.HTTPException):
            # `from None`: the transport's own exception can name the host.
            raise AppointmentsUnavailable("no_answer") from None
        return map_answer(response)


def build_list_client(env: Mapping[str, str] | None = None) -> AppointmentListClient | None:
    """From the same two variables as sub-project 10's gateway, or None (design D8: no mock list)."""
    env = os.environ if env is None else env
    url = env.get("APPOINTMENT_SERVICE_URL", "").strip()
    key = env.get("APPOINTMENT_API_KEY", "").strip()
    if not url or not key:
        return None
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return AppointmentListClient(url, key)
```

Note: `status_404` never appears (a 404 is either `PatientNotFound` or `invalid_response`) — the parametrized test pins that.

- [ ] **Step 4: Run** — targeted tests pass, then the whole backend suite once.
- [ ] **Step 5: Commit** — `git add backend && git commit -m "Add the appointment list client, the contract or nothing" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`

---

### Task 3: hospital-agent — the two routes, wiring and `docs/api.md` §9

**Files:**
- Create: `backend/hospital_agent/api/appointments.py` (window parsing + the shared read)
- Modify: `backend/hospital_agent/api/app.py` (`create_app(..., appointment_list=None)`; `app.state.appointment_list`)
- Modify: `backend/hospital_agent/api/routes_patient.py`, `backend/hospital_agent/api/routes_staff.py`, `backend/hospital_agent/api/schemas.py`
- Create: `backend/tests/test_api_appointments.py`
- Modify: `docs/api.md` (route table §2 and a new §9)

**Interfaces:**
- Consumes: Task 2's `AppointmentListClient`, `AppointmentsUnavailable`, `PatientNotFound`, `build_list_client`.
- Produces: `GET /api/patient/appointments?from=&to=` and `GET /api/staff/cases/{case_id}/appointments?from=&to=` → `AppointmentsView {from, to, appointments: [AppointmentView], truncated}`; `AppointmentView {appointment_id, appointment_at, department, doctor_name, location, status, required_documents}`; codes as in Global Constraints. `api.appointments.DEFAULT_SPAN = timedelta(days=30)`, `MAX_SPAN = timedelta(days=366)`.

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_api_appointments.py`:

```python
"""Sub-project 16 (design D5, D6, D9): the patient's and the staff's appointment routes."""
import logging
from datetime import UTC, datetime, timedelta

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
    assert (body["from"], body["to"]) == ("2026-09-30T21:00:00Z", "2026-10-31T22:00:00Z")


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
    caplog.set_level(logging.DEBUG)
    with make(app_engine, FakeList(raises=AppointmentsUnavailable("no_answer"))) as client:
        client.get("/api/patient/appointments", headers=auth(client, PATIENT))
    assert "appointments_unavailable" in caplog.text or "no_answer" in caplog.text
    assert PATIENT not in caplog.text and "APT-" not in caplog.text
```

(If `/api/staff/cases/{id}/context` answers only for a case in review, use the fixture pattern `test_api_staff.py` uses to get a case into review, or compare `shown_context_ref` on a case that the context route does answer for — keep the "unchanged by the appointments read" assertion.)

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Implement.** `backend/hospital_agent/api/schemas.py`:

```python
class AppointmentView(BaseModel):
    """One appointment, as the appointment-service answered it (sub-project 16, design D6)."""
    model_config = ConfigDict(from_attributes=True)

    appointment_id: str
    appointment_at: datetime
    department: str
    doctor_name: str | None
    location: str | None
    status: Literal["Scheduled", "Cancelled"]
    required_documents: list[str]


class AppointmentsView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    window_from: datetime = Field(alias="from")
    window_to: datetime = Field(alias="to")
    appointments: list[AppointmentView]
    truncated: bool
```

(Check that `datetime` answers serialise as `…Z` for UTC the way the other views in `schemas.py` do; if the project's convention differs, follow it and adjust the test's expected strings — keep them exact.)

`backend/hospital_agent/api/appointments.py`:

```python
"""Sub-project 16 (design D5, D6, D9): the window and the one read both appointment routes share."""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from ..appointment_list import AppointmentListClient, AppointmentsUnavailable, PatientNotFound
from .schemas import AppointmentsView, AppointmentView

DEFAULT_SPAN = timedelta(days=30)
MAX_SPAN = timedelta(days=366)
logger = logging.getLogger(__name__)


def _instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid_range") from None
    if parsed.tzinfo is None:
        raise HTTPException(status_code=422, detail="invalid_range")
    return parsed


def window(start: str | None, end: str | None, now: datetime) -> tuple[datetime, datetime]:
    """`from` inclusive, `to` exclusive. Neither: now .. now+30d; one: 30 days from/to it."""
    low = _instant(start) if start is not None else None
    high = _instant(end) if end is not None else None
    if low is None and high is None:
        low = now
    if high is None:
        high = low + DEFAULT_SPAN
    if low is None:
        low = high - DEFAULT_SPAN
    if not low < high or high - low > MAX_SPAN:
        raise HTTPException(status_code=422, detail="invalid_range")
    return low, high


def read(client: AppointmentListClient | None, patient_id: str, start: str | None,
         end: str | None) -> AppointmentsView:
    low, high = window(start, end, datetime.now(UTC))
    if client is None:
        raise HTTPException(status_code=404, detail="appointments_not_enabled")
    started = time.perf_counter()
    try:
        result = client.list(patient_id, low, high)
    except PatientNotFound:
        logger.info("appointment list: patient_not_found")
        raise HTTPException(status_code=404, detail="patient_not_found") from None
    except AppointmentsUnavailable as failure:
        logger.warning("appointment list unavailable: %s", failure.code)
        raise HTTPException(status_code=503, detail="appointments_unavailable") from None
    logger.info("appointment list: %d rows in %d ms", len(result.appointments),
                round((time.perf_counter() - started) * 1000))
    return AppointmentsView(window_from=low, window_to=high, truncated=result.truncated,
                            appointments=[AppointmentView.model_validate(a) for a in result.appointments])
```

`AppointmentView.model_validate(a)` reads the dataclass by attribute; `required_documents` is a tuple — convert (`list(a.required_documents)`) if pydantic refuses it.

`api/app.py`: add the parameter `appointment_list: AppointmentListClient | None = None` to `create_app`, and in the lifespan beside `document_intake`: `app.state.appointment_list = build_list_client() if owned else appointment_list` (import `build_list_client` and the type).

`api/routes_patient.py` (import `Query`, `Request`; `from . import appointments`; `AppointmentsView`):

```python
@router.get("/appointments", response_model=AppointmentsView)
def list_appointments(request: Request, start: str | None = Query(default=None, alias="from"),
                      end: str | None = Query(default=None, alias="to"),
                      principal: Principal = Depends(require_patient)) -> AppointmentsView:
    """The patient's own appointments (sub-project 16): the patient is the token's, never a parameter."""
    return appointments.read(request.app.state.appointment_list, principal.patient_id, start, end)
```

`api/routes_staff.py`:

```python
@router.get("/cases/{case_id}/appointments", response_model=AppointmentsView)
def case_appointments(case_id: str, request: Request, start: str | None = Query(default=None, alias="from"),
                      end: str | None = Query(default=None, alias="to"),
                      db: Engine = Depends(get_engine)) -> AppointmentsView:
    """The case's patient's appointments (sub-project 16). Apart from /context on purpose: the
    list is not part of what a decision is bound to (shown_context_ref)."""
    with db.connect() as conn:
        case = repository.load_case(conn, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case_not_found")
    return appointments.read(request.app.state.appointment_list, case.patient_id, start, end)
```

(Check `response_model_by_alias` — FastAPI's default is `True`, so `from`/`to` go out under their aliases; the test pins it.)

- [ ] **Step 4: `docs/api.md`** — add both routes to the §2 route table, and a new `## 9. The patient's appointments (sub-project 16)` with: both routes, the query (`from` inclusive / `to` exclusive, ISO with offset, defaults, 366-day cap), the answer (with an example), `truncated` (the service returns at most 100), every code (`401`, `403 patients_only` / `staff_only`, `404 appointments_not_enabled`, `404 case_not_found`, `404 patient_not_found`, `422 invalid_range`, `503 appointments_unavailable`), and one line that the list is read live from the appointment-service and never stored (row 89).

- [ ] **Step 5: Run** — targeted, then the whole backend suite once.
- [ ] **Step 6: Commit** — `git add backend docs/api.md && git commit -m "Let the patient and the staff read the patient's appointments" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`

---

### Task 4: frontend — types, client and the `AppointmentsPanel`

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/api/client.test.ts`
- Create: `frontend/src/components/AppointmentsPanel.tsx`, `frontend/src/components/AppointmentsPanel.test.tsx`
- Modify: `frontend/src/styles/app.css` (new selectors)

**Interfaces:**
- Produces: types `AppointmentStatus = 'Scheduled' | 'Cancelled'`, `Appointment`, `AppointmentList { from, to, appointments, truncated }`; client `listMyAppointments(from: Date, to: Date): Promise<AppointmentList>` and `listCaseAppointments(caseId: string, from: Date, to: Date): Promise<AppointmentList>` (both send `toISOString()`); component `AppointmentsPanel({ audience: 'patient' | 'staff', load: (from: Date, to: Date) => Promise<AppointmentList> })`; helpers exported from the component file: `defaultRange(today: Date): { from: string; to: string }` (`YYYY-MM-DD`, today and today+30), `rangeToInstants(from: string, to: string): { from: Date; to: Date }` (local midnight of `from`, local midnight of the day **after** `to` — the `to` day is included), `DEPARTMENT_LABELS`.

- [ ] **Step 1: Types and client, test first.** In `client.test.ts` add (following the file's existing fetch-stub pattern): `listMyAppointments` → `GET /api/patient/appointments?from=…&to=…` with the two ISO strings; `listCaseAppointments('C 1', …)` → `/api/staff/cases/C%201/appointments?from=…&to=…`. Then add to `types.ts`:

```ts
// ---- Appointments (sub-project 16, docs/api.md §9) --------------------------

export type AppointmentStatus = 'Scheduled' | 'Cancelled'

export interface Appointment {
  appointment_id: string
  appointment_at: IsoDateTime
  department: string
  doctor_name: string | null
  location: string | null
  status: AppointmentStatus
  required_documents: string[]
}

export interface AppointmentList {
  from: IsoDateTime
  to: IsoDateTime
  appointments: Appointment[]
  truncated: boolean
}
```

and to `client.ts`:

```ts
// ---- Appointments (sub-project 16) -------------------------------------------

function windowQuery(from: Date, to: Date): string {
  return new URLSearchParams({ from: from.toISOString(), to: to.toISOString() }).toString()
}

/** `GET /api/patient/appointments` - the token's patient. `from` inclusive, `to` exclusive. */
export function listMyAppointments(from: Date, to: Date): Promise<AppointmentList> {
  return request<AppointmentList>('GET', `/patient/appointments?${windowQuery(from, to)}`)
}

/** `GET /api/staff/cases/{case_id}/appointments` - the case's patient. */
export function listCaseAppointments(caseId: string, from: Date, to: Date): Promise<AppointmentList> {
  return request<AppointmentList>('GET', `/staff/cases/${id(caseId)}/appointments?${windowQuery(from, to)}`)
}
```

- [ ] **Step 2: The panel's tests** — `AppointmentsPanel.test.tsx` (Vitest + Testing Library, no network; `vi.useFakeTimers({ toFake: ['Date'] })` / `vi.setSystemTime(new Date(2026, 8, 26, 10, 0))` for "today"):

1. On mount it calls `load` once with `from` = 2026-09-26 00:00 local and `to` = 2026-10-27 00:00 local (today + 30 days, the `to` day included), and the two date inputs show `2026-09-26` and `2026-10-26`.
2. It lists an appointment: the formatted date and time, `נוירולוגיה` for `Neurology`, the doctor, the location, the status label `מתוכנן`, and `ספירת דם מלאה` for `CBC`; a `Cancelled` one shows `בוטל`. An unknown department is shown as sent.
3. Changing the dates and pressing `הצגה` calls `load` again with the new range.
4. `to` before `from` shows `תאריך הסיום חייב להיות אחרי תאריך ההתחלה או באותו יום` and does not call `load`; a span over 366 days shows `אפשר להציג עד שנה אחת` and does not call `load`.
5. An empty list shows `אין תורים בטווח שנבחר.`
6. `truncated: true` shows `מוצגים 100 התורים הראשונים בטווח. אפשר לצמצם את הטווח.`
7. A rejected `load` (`new ApiError(503, 'appointments_unavailable')`) shows an error with a `נסה שוב` button; clicking it calls `load` again with the same range. With `audience='patient'` the alert text contains **no** `appointments_unavailable`; with `audience='staff'` it shows the Hebrew label and the code in `.mono`.
8. `ApiError(404, 'appointments_not_enabled')` shows `רשימת התורים אינה זמינה כרגע.` and no retry button.
9. `ApiError(404, 'patient_not_found')`, patient audience: `לא נמצאו פרטי המטופל במערכת התורים. אפשר לפנות למוקד המטופלים.` (no code).

Read `ApiError`'s real constructor in `client.ts` and build the errors the way other tests do.

- [ ] **Step 3: Run to see them fail.**

- [ ] **Step 4: Implement `AppointmentsPanel.tsx`.** Requirements (write the code to these; reuse `TextField`, `Button`, `Alert` from `components/`, and the `.field`/`.label`/`.control` markup `TextField` already renders):

- State: `range` (two `YYYY-MM-DD` strings, initial `defaultRange(new Date())`), `shown` (the range last loaded), `result: AppointmentList | null`, `error: unknown | null`, `busy`, `rangeError: string | null`.
- `defaultRange(today)`: local date of `today` and of `today` + 30 days (build with `new Date(y, m, d + 30)`, format with zero-padded local parts — never `toISOString()`, which would shift the day).
- `rangeToInstants(from, to)`: `new Date(fy, fm - 1, fd)` and `new Date(ty, tm - 1, td + 1)`.
- Validation before loading: `to` day before `from` day → the rangeError above; `rangeToInstants` span over 366 days → the one-year sentence.
- `useEffect` on mount loads the default range; a `form` with the two `TextField type="date"` (`label` `מתאריך` / `עד תאריך`, `dir="ltr"`) and a `Button type="submit"` `הצגה`; `onSubmit` prevents default and loads.
- Ignore a stale answer: keep a request counter in a ref and apply an answer only when it is the latest.
- Heading: `h2` `התורים שלי` for the patient, `התורים של המטופל` for staff.
- Each appointment is an `li` with: `<time dateTime={appointment_at}>` formatted with `Intl.DateTimeFormat('he-IL', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' })`; the department label (`DEPARTMENT_LABELS[department] ?? department`); doctor and location when present; the status (`מתוכנן` / `בוטל`; staff also shows the code in `.mono` beside it); the required documents as their Hebrew labels (import `documentLabel` from `../pages/patient/helpers` — it returns `string | null`; fall back to the code; staff shows the code in `.mono` beside each label). A cancelled item gets the class `is-cancelled`.
- Errors (use `detailOf`-style reading of `ApiError.detail`):
  - `appointments_not_enabled` → info `Alert` `רשימת התורים אינה זמינה כרגע.`, no retry.
  - otherwise an error `Alert` titled `לא הצלחנו לטעון את התורים` with a `נסה שוב` button that reloads `shown`. Body: patient — `patient_not_found` → the sentence in test 9; anything else → `מערכת התורים אינה זמינה כרגע. נסו שוב בעוד רגע.`; staff — a Hebrew label (`appointments_unavailable`: `מערכת התורים אינה זמינה`, `patient_not_found`: `המטופל אינו מוכר במערכת התורים`, `case_not_found`: `הפנייה לא נמצאה`, `invalid_range`: `טווח תאריכים לא תקין`, otherwise the code itself) followed by the code in `<span className="mono">`.
- `DEPARTMENT_LABELS`: `{ Cardiology: 'קרדיולוגיה', Dermatology: 'עור', Neurology: 'נוירולוגיה', Ophthalmology: 'עיניים', Orthopedics: 'אורתופדיה' }` (the appointment-service catalog, `app/catalog.py`).

CSS (new selectors only, logical properties, tokens that exist — verify each `var(--…)` in `tokens.css`): `.appointments` (card spacing), `.appointments-filter` (flex, wrap, gap, align-items end), `.appointment-list` (no list style, column gap), `.appointment-item` (padding, radius, surface background), `.appointment-item.is-cancelled` (muted ink, the date struck through with `text-decoration: line-through`), `.appointment-meta` (small, muted).

- [ ] **Step 5: Run** `npm test` and `npm run build` — all pass.
- [ ] **Step 6: Commit** — `git add frontend/src && git commit -m "Add the appointments panel with a day-range filter" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`

---

### Task 5: frontend — the panel on both screens

**Files:**
- Modify: `frontend/src/pages/patient/MyRequests.tsx`, `frontend/src/pages/patient/MyRequests.test.tsx`
- Modify: `frontend/src/pages/staff/ReviewCase.tsx`, `frontend/src/pages/staff/ReviewCase.test.tsx`

**Interfaces:**
- Consumes: Task 4's `AppointmentsPanel`, `listMyAppointments`, `listCaseAppointments`.

- [ ] **Step 1: Tests first.**
  - `MyRequests.test.tsx`: stub `api.listMyAppointments` in the file's existing mocking style (resolve `{ from, to, appointments: [one], truncated: false }`) for every test; add a test that the page shows the heading `התורים שלי` **above** the `הפניות שלי` heading (compare `compareDocumentPosition`) and lists the appointment; and a test that a failing appointments load does not hide the requests list.
  - `ReviewCase.test.tsx`: stub `api.listCaseAppointments` for every test; add a test that the review screen shows `התורים של המטופל` and that `listCaseAppointments` was called with the case id.
- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement.**
  - `MyRequests.tsx`: return a fragment — `<AppointmentsPanel audience="patient" load={api.listMyAppointments} />` first, then the existing `section.card` unchanged. Update the component's doc comment.
  - `ReviewCase.tsx`: after the `</header>` and before the `loadError` alert, `<AppointmentsPanel audience="staff" load={(from, to) => api.listCaseAppointments(context.case_id, from, to)} />`. Memoise the `load` callback with `useCallback` on `context.case_id` so a context refresh does not reload the list.
- [ ] **Step 4: Run** `npm test` and `npm run build`.
- [ ] **Step 5: Commit** — `git add frontend/src && git commit -m "Show the appointments on the patient's main screen and the case screen" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`

---

### Task 6: Verification and documentation

**Files:**
- Modify: `CLAUDE.md` (hospital-agent worktree)

- [ ] **Step 1:** appointment-service full suite (the `appt-test` command) — report the count.
- [ ] **Step 2:** hospital-agent full backend suite; `python -m obs.golden` (35 / 4 / 54); `python -m hospital_agent.policy.consistency` (7 properties, 9 UNSAT); `pytest tests/test_fsm.py::test_table_matches_spec_3_row_by_row`.
- [ ] **Step 3:** frontend `npm test` and `npm run build`.
- [ ] **Step 4: CLAUDE.md** — in *Project status*, after the sub-project 15 paragraph: "Sub-project 16 (`docs/superpowers/specs/2026-09-26-appointment-list-design.md`, `docs/spec_corrections.md` row 89) shows the patient's appointments: the appointment-service's `GET /api/v1/patients/{id}/appointments?from=&to=` (both statuses, at most 100, `truncated`), read by `hospital_agent/appointment_list.py` - a second recorded exception beside row 79, read-only and outside the FSM - through `GET /api/patient/appointments` and `GET /api/staff/cases/{id}/appointments` (`docs/api.md` §9; default now..+30 days, at most 366), and the shared `AppointmentsPanel` on "הפניות שלי" and the review screen. Without `APPOINTMENT_SERVICE_URL`/`APPOINTMENT_API_KEY` the routes answer `404 appointments_not_enabled` - there is no mock list." In *Working in the backend*, in the `upload_pdf` bullet's list of exceptions, mention `appointment_list.py` (row 89).
- [ ] **Step 5: Commit** — `git add CLAUDE.md && git commit -m "Record sub-project 16 in the project status" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`
- [ ] **Step 6:** keep the `appts-proto` stack for the final review; `down -v` after it.
