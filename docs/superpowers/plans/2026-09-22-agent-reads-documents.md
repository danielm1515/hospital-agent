# Sub-project 13: The Hospital Agent reads required and held documents - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the owner's stack, a case's required documents come from the appointment-service (per appointment), its held documents from the document-service (the patient's accepted, still-valid PDFs), and the patient uploads a PDF from the agent's own screen, which the agent forwards to the document-service - all on the existing states, events and readiness flow.

**Architecture:** (1) `required_documents` moves to `CheckAppointment`'s result fields (the mock moves with it, so the §0 scenarios and golden traces keep their row counts). (2) A `DocumentServiceGateway` answers `CheckDocuments` from the document-service's listing; `build_gateway()` composes appointments -> documents -> mock. (3) A `DocumentIntakeClient` lets the Session Service forward the patient's PDF; an accepted, required document becomes the existing `DOCUMENT_UPLOADED` (its `document_id` is the document **type**, because `HOLD_DOCUMENT` appends `document_id` to `held_documents`), with only a reference line in the Data Log. (4) The patient UI gets a PDF picker and a Hebrew message per upload result.

**Tech Stack:** Python 3.13 / FastAPI (backend, standard-library HTTP only), React + TypeScript + Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-09-22-document-requirements-design.md` §2, §5, §6 - and the document-service's actual contract (`C:/Users/danie/Documents/ChatGPT/לימודים פרויקט גמר/document-service/README.md`, "API"): `GET /api/v1/patients/{id}/documents` -> `{"documents": [{"document_id","document_type","document_date","result","valid_until","uploaded_at"}]}` with `result` evaluated as of today; `POST` (multipart `file`) -> `201 {"document_id","document_type","document_date","result"[, "duplicate_of"]}`; errors `{"error": code}`; worst-case upload latency 70 s.

## Global Constraints

- No new State, Event, Action or escalation kind; no change to OPA rules, `flows.dl`, the Temporal Monitor or Z3. `ACTION_TARGETS` unchanged: `CheckDocuments` sends only `patient_id` (spec §11).
- Without `DOCUMENT_SERVICE_URL` (and without `APPOINTMENT_SERVICE_URL`) behaviour stays today's except the documented ownership move; the three §0 scenarios still pass and `python -m obs.golden` still prints **35 / 4 / 54**.
- No new runtime dependency. Keys and URLs never reach a log, repr, exception, `ToolResult`, Audit row or `/health` (which shows only the words `mock` / `appointment-service` / `document-service`).
- The patient UI never shows an escalation kind, a policy reason or an Audit row (§12.3); upload results are shown as Hebrew sentences.
- The Data Log never holds a PDF's text: an accepted upload records only a reference line.
- Code, identifiers, comments, commit messages and repo docs in English; UI text Hebrew. Every commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Never read, print or commit any `.env`; never push; never touch the owner's running stack.
- Work in a worktree; run backend tests only in its isolated compose project, from the worktree root:
  `docker compose -p hospital-sp13 -f docker-compose.yml -f .superpowers/sdd/2026-09-22-agent-reads-documents/no-ports.yml run --rm backend pytest <args>`
  and frontend tests with `cd frontend && npm test` (Node 22; no network).

---

### Task 1: Shared HTTP helpers, and `required_documents` from the appointment system

**Files:**
- Create: `backend/hospital_agent/execution/http.py`
- Modify: `backend/hospital_agent/execution/appointment_service.py` (use `http.py`; map `required_documents`)
- Modify: `backend/hospital_agent/execution/gateway.py` (`RESULT_FIELDS`; `MockGateway`)
- Modify tests: `backend/tests/test_gateway.py`, `backend/tests/test_appointment_service.py`, `backend/tests/test_appointment_service_e2e.py`, and any other test the move breaks (see Step 5)

**Interfaces:**
- Produces: `http.HttpResponse(status, body)`, `http.MAX_BODY_BYTES`, `http.request(method, url, headers, body, timeout) -> HttpResponse` (raises `OSError` / `http.client.HTTPException` when there is no usable answer; never follows a redirect; never uses a proxy; reads at most `MAX_BODY_BYTES`), `http.no_answer_result(exc) -> ToolResult` (timeout -> `transient_failure "timeout"`, other `OSError` -> `"unavailable"`, `HTTPException` -> `error "invalid_response"`). `appointment_service` keeps exporting `HttpResponse`, `urllib_transport`, `MAX_BODY_BYTES`, `TIMEOUT_SECONDS`, `map_response` so existing imports keep working.

- [ ] **Step 1: Move the transport.** Create `backend/hospital_agent/execution/http.py` by moving, unchanged in behaviour, from `appointment_service.py`: `HttpResponse`, `MAX_BODY_BYTES` (and its comment), `_NoRedirect`, the opener (`build_opener(ProxyHandler({}), _NoRedirect)`), and the body of `urllib_transport`, generalised as:

```python
def request(method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as answer:
            return HttpResponse(answer.status, answer.read(MAX_BODY_BYTES))
    except urllib.error.HTTPError as exc:  # a status the server did send is an answer, not a failure
        with exc:
            return HttpResponse(exc.code, exc.read(MAX_BODY_BYTES))
```

and add

```python
def no_answer_result(exc: BaseException) -> ToolResult:
    """What a call that got no usable HTTP answer means (sub-project 10 design §2.6)."""
    if isinstance(exc, http.client.HTTPException):
        return ToolResult(ERROR, {"error": "invalid_response"})
    timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
    return ToolResult(TRANSIENT_FAILURE, {"error": "timeout" if timed_out else "unavailable"})
```

(import `ERROR`, `TRANSIENT_FAILURE`, `ToolResult` from `.gateway`; `import http.client` as `http_client` if the module name clashes). In `appointment_service.py` keep `def urllib_transport(url, headers, timeout): return http.request("GET", url, headers, None, timeout)` and re-export `HttpResponse` and `MAX_BODY_BYTES` from `http.py`; replace its two `except` branches in `call()` with `except (OSError, http_client.HTTPException) as exc: return no_answer_result(exc)`. All existing appointment-service tests must pass unchanged at this step - run them.

- [ ] **Step 2: Failing tests for the ownership move.**

In `backend/tests/test_gateway.py`, replace the `docs.data` assertion in `test_mock_returns_the_demo_data` and add:

```python
def test_the_mock_appointment_carries_the_requirements_and_the_mock_documents_only_what_is_held():
    gw = MockGateway(clock=lambda: NOW)
    assert gw.call("CheckAppointment", {"patient_id": "P"}, "k").data == {
        "appointment_at": NOW + timedelta(hours=96), "required_documents": ["referral", "blood_test"]}
    assert gw.call("CheckDocuments", {"patient_id": "P"}, "k").data == {"held_documents": ["referral"]}


def test_each_system_supplies_only_its_own_facts():
    """Design §5.1: the appointment system owns the requirements, the document system what is held."""
    assert RESULT_FIELDS["CheckAppointment"] == ("appointment_at", "required_documents")
    assert RESULT_FIELDS["CheckDocuments"] == ("held_documents",)
```

In `backend/tests/test_appointment_service.py` extend `FOUND["appointment"]` with `"required_documents": ["COAGULATION_TESTS", "CBC", "ECG"]` and add:

```python
def test_a_found_appointment_carries_its_required_documents_sorted():
    assert map_response(response(200, FOUND), now=NOW).data["required_documents"] == ["CBC", "COAGULATION_TESTS", "ECG"]


def test_an_appointment_with_no_requirements_needs_nothing():
    body = {"found": True, "appointment": {**FOUND["appointment"], "required_documents": []}}
    assert map_response(response(200, body), now=NOW).data["required_documents"] == []


@pytest.mark.parametrize("value", [None, "CBC", [""], [1], ["CBC", None]])
def test_malformed_requirements_are_an_invalid_response(value):
    appointment = {**FOUND["appointment"], "required_documents": value}
    assert map_response(response(200, {"found": True, "appointment": appointment}), now=NOW) == \
        ToolResult(ERROR, {"error": "invalid_response"})


def test_an_appointment_service_without_the_field_is_an_invalid_response():
    """An older appointment-service (before sub-project 11) cannot say what is required: fail closed."""
    appointment = {k: v for k, v in FOUND["appointment"].items() if k != "required_documents"}
    assert map_response(response(200, {"found": True, "appointment": appointment}), now=NOW).data == {"error": "invalid_response"}
```

(Use the file's existing fixed `NOW`; if its name differs, use that name.) Update the existing assertions in that file and in `test_appointment_service_e2e.py` whose expected `ToolResult`/answers lack `required_documents`: every found-appointment answer in the e2e file gains `"required_documents": ["referral", "blood_test"]`, and assertions on `ok` data include it.

- [ ] **Step 3: Run to see them fail.**

- [ ] **Step 4: Implement.**
  - `gateway.py`: `RESULT_FIELDS[CHECK_APPOINTMENT] = ("appointment_at", "required_documents")`, `RESULT_FIELDS[CHECK_DOCUMENTS] = ("held_documents",)`, and a comment: "design §5.1 (sub-projects 11-13): the appointment system owns what an appointment requires, the document system what the patient holds". `MockGateway`'s `CheckAppointment` returns `{"appointment_at": at, "required_documents": list(self.required_documents)}`; its `CheckDocuments` returns `{"held_documents": list(self.held_documents)}`. Update its docstring.
  - `appointment_service.map_response`: after the time checks, read `appointment.get("required_documents")`; it must be a list of non-empty strings (else `invalid_response`); put `sorted(set(...))` into the `ok` data next to `appointment_at`.

- [ ] **Step 5: Run the whole backend suite.** Fix every test the move broke by moving the requirement to where the appointment step supplies it - never by weakening an assertion. Known places: `tests/test_execution_core.py:146` (a CheckDocuments result with `required_documents` - it is now dropped by `RESULT_FIELDS`; adjust the test's intent to "a result field outside the action is dropped" if that is what it checks), `tests/test_orchestrator.py:201` and `tests/test_retrieved_content.py:29` (`MockGateway(required_documents=..., held_documents=...)` keeps working - the constructor is unchanged), `tests/driver.py:163` (emits DATA_RETRIEVED directly; leave it). Then run `docker compose ... run --rm backend python -m obs.golden` -> must print 35, 4, 54, and `... python -m hospital_agent.policy.consistency` -> `7 abstract properties passed (9 UNSAT queries)`.

- [ ] **Step 6: Commit** `Move the required documents to the appointment system, and share the HTTP helpers`.

---

### Task 2: `CheckDocuments` against the document-service

**Files:**
- Create: `backend/hospital_agent/execution/document_service.py`
- Modify: `backend/hospital_agent/execution/appointment_service.py` (`build_gateway(env, fallback=None)`)
- Modify: `backend/hospital_agent/api/app.py` (compose the gateways; `/health` gains `documents`)
- Modify: `docker-compose.yml` (backend: `DOCUMENT_SERVICE_URL`, `DOCUMENT_API_KEY`), `docs/api.md` (`/health`)
- Modify: `backend/tests/conftest.py` (clear `DOCUMENT_SERVICE_URL`, `DOCUMENT_API_KEY` too)
- Test: `backend/tests/test_document_service.py`, `backend/tests/test_documents_e2e.py`

**Interfaces:**
- Consumes: Task 1's `http.request`, `http.no_answer_result`, `HttpResponse`.
- Produces: `DocumentServiceGateway(base_url, api_key, *, fallback=None, transport=None, timeout=10.0)` (`call`, `idempotent`, `fallback`, `calls`, a `__repr__` without URL/key); `map_documents(response) -> ToolResult`; `build_document_gateway(env=None, fallback=None) -> tuple[ToolGateway | None, str]` with `"mock"` / `"document-service"` / `"disabled: DOCUMENT_API_KEY is not set"` / `"disabled: DOCUMENT_SERVICE_URL is not an http(s) URL"`; `build_gateway(env=None, fallback=None)` returns `fallback` (or a new `MockGateway`) when `APPOINTMENT_SERVICE_URL` is unset.

- [ ] **Step 1: Failing tests** - `backend/tests/test_document_service.py`:

```python
"""Sub-project 13: CheckDocuments against the owner's document-service (design §5.2)."""
import json

import pytest

from hospital_agent.execution.document_service import (
    DocumentServiceGateway, build_document_gateway, map_documents,
)
from hospital_agent.execution.gateway import ERROR, OK, TRANSIENT_FAILURE, MockGateway, ToolResult
from hospital_agent.execution.http import HttpResponse

KEY = "doc-key-1a2b"


def listing(*items):
    return HttpResponse(200, json.dumps({"documents": list(items)}).encode())


def item(doc_type, result="ACCEPTED", doc_id="DOC-1"):
    return {"document_id": doc_id, "document_type": doc_type, "document_date": "2026-09-15",
            "result": result, "valid_until": "2026-12-14", "uploaded_at": "2026-09-22T10:00:00+00:00"}


def test_held_documents_are_the_types_of_the_accepted_ones_sorted_and_unique():
    answer = listing(item("ECG"), item("CBC"), item("CBC", doc_id="DOC-2"), item("URINALYSIS", "DOCUMENT_EXPIRED"),
                     item(None, "NON_MEDICAL_DOCUMENT"), item("CBC", "DUPLICATE_DOCUMENT"))
    assert map_documents(answer) == ToolResult(OK, {"held_documents": ["CBC", "ECG"]})


def test_no_documents_holds_nothing():
    assert map_documents(listing()) == ToolResult(OK, {"held_documents": []})


@pytest.mark.parametrize("answer", [
    HttpResponse(200, b"not json"), HttpResponse(200, b"[]"), HttpResponse(200, b'{"documents": {}}'),
    listing({"document_type": "CBC"}), listing({"result": "ACCEPTED", "document_type": 5}),
    listing({"result": "ACCEPTED", "document_type": ""}), HttpResponse(404, b'{"error": "not_found"}'),
    HttpResponse(302, b""), HttpResponse(400, b'{"error": "validation_error"}'),
])
def test_anything_else_is_an_invalid_response(answer):
    assert map_documents(answer) == ToolResult(ERROR, {"error": "invalid_response"})


@pytest.mark.parametrize("status, error", [(500, "unavailable"), (502, "unavailable"), (503, "unavailable"), (504, "timeout")])
def test_a_server_side_failure_is_transient(status, error):
    assert map_documents(HttpResponse(status, b'{"error": "x"}')) == ToolResult(TRANSIENT_FAILURE, {"error": error})


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_is_unauthorized(status):
    assert map_documents(HttpResponse(status, b'{"error": "unauthorized"}')) == ToolResult(ERROR, {"error": "unauthorized"})


class Transport:
    def __init__(self, answer=None, raises=None):
        self.answer, self.raises, self.requests = answer, raises, []

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append((method, url, dict(headers), body, timeout))
        if self.raises:
            raise self.raises
        return self.answer


def test_only_the_patient_id_leaves_quoted_with_the_key():
    transport = Transport(listing())
    gw = DocumentServiceGateway("http://docs.test/", KEY, transport=transport)
    gw.call("CheckDocuments", {"patient_id": "P 1/x"}, "CASE:2:0:1")
    [(method, url, headers, body, timeout)] = transport.requests
    assert (method, url, body) == ("GET", "http://docs.test/api/v1/patients/P%201%2Fx/documents", None)
    assert headers == {"X-API-Key": KEY, "Accept": "application/json"}
    assert timeout == 10.0


def test_the_other_actions_are_the_fallbacks():
    fallback = MockGateway()
    gw = DocumentServiceGateway("http://docs.test", KEY, fallback=fallback, transport=Transport(listing()))
    assert gw.call("LoadInstructions", {}, "k") == MockGateway().call("LoadInstructions", {}, "k")
    assert gw.idempotent("CheckDocuments") == fallback.idempotent("CheckDocuments")


@pytest.mark.parametrize("raised, expected", [
    (TimeoutError(), ToolResult(TRANSIENT_FAILURE, {"error": "timeout"})),
    (ConnectionRefusedError(), ToolResult(TRANSIENT_FAILURE, {"error": "unavailable"})),
])
def test_no_answer_is_transient(raised, expected):
    gw = DocumentServiceGateway("http://docs.test", KEY, transport=Transport(raises=raised))
    assert gw.call("CheckDocuments", {"patient_id": "P-1"}, "k") == expected


def test_a_missing_patient_id_is_never_sent():
    transport = Transport(listing())
    assert DocumentServiceGateway("http://docs.test", KEY, transport=transport).call("CheckDocuments", {}, "k").kind == ERROR
    assert transport.requests == []


def test_the_key_and_url_never_show():
    gw = DocumentServiceGateway("http://docs.test", KEY, transport=Transport(raises=OSError(KEY)))
    result = gw.call("CheckDocuments", {"patient_id": "P-1"}, "k")
    assert KEY not in repr(result) and KEY not in repr(gw) and "docs.test" not in repr(gw)


@pytest.mark.parametrize("env", [{}, {"DOCUMENT_SERVICE_URL": " "}, {"DOCUMENT_API_KEY": KEY}])
def test_without_a_url_it_is_the_fallback(env):
    fallback = MockGateway()
    gw, source = build_document_gateway(env, fallback=fallback)
    assert gw is fallback and source == "mock"


def test_a_url_without_a_key_refuses():
    assert build_document_gateway({"DOCUMENT_SERVICE_URL": "http://h:8090"}) == (None, "disabled: DOCUMENT_API_KEY is not set")


@pytest.mark.parametrize("url", ["h:8090", "ftp://h", "http://"])
def test_a_non_http_url_refuses(url):
    gw, status = build_document_gateway({"DOCUMENT_SERVICE_URL": url, "DOCUMENT_API_KEY": KEY})
    assert gw is None and status == "disabled: DOCUMENT_SERVICE_URL is not an http(s) URL"


def test_a_url_and_a_key_give_the_document_service():
    gw, source = build_document_gateway({"DOCUMENT_SERVICE_URL": "http://h:8090", "DOCUMENT_API_KEY": KEY})
    assert isinstance(gw, DocumentServiceGateway) and source == "document-service"
```

Append to `backend/tests/test_appointment_service.py`:

```python
def test_build_gateway_falls_back_to_the_given_gateway():
    fallback = MockGateway()
    gw, source = build_gateway({}, fallback=fallback)
    assert gw is fallback and source == "mock"
    gw, _ = build_gateway({"APPOINTMENT_SERVICE_URL": "http://h:1", "APPOINTMENT_API_KEY": KEY}, fallback=fallback)
    assert gw.fallback is fallback
```

`backend/tests/test_documents_e2e.py` - through the real Orchestrator (FakeProvider), Tool Executor and State Manager, with both new gateways over fake transports (mirror `tests/test_orchestrator.py`'s `run` fixture and its `_until` helper - import `_until` from `tests.test_orchestrator` or copy it):

```python
"""Sub-project 13 end to end: required documents from the appointment system, held ones from the
document system, and the existing readiness flow deciding (design §5.4)."""
import json

from hospital_agent.execution.appointment_service import AppointmentServiceGateway
from hospital_agent.execution.document_service import DocumentServiceGateway
from hospital_agent.execution.http import HttpResponse
from hospital_agent.llm.orchestrator import Orchestrator
from hospital_agent.llm.provider import FakeProvider
from hospital_agent.naming import State
from hospital_agent.scripted import ScriptedAgents
from tests.test_orchestrator import _until


def appointment(required):
    body = {"found": True, "appointment": {"appointment_at": "2030-10-03T10:30:00+03:00", "status": "Scheduled",
                                           "required_documents": required}}
    return lambda url, headers, timeout: HttpResponse(200, json.dumps(body).encode())


class Documents:
    def __init__(self, *types):
        self.types = list(types)

    def __call__(self, method, url, headers, body, timeout):
        docs = [{"document_id": f"DOC-{i}", "document_type": t, "document_date": "2030-09-15", "result": "ACCEPTED",
                 "valid_until": "2030-12-14", "uploaded_at": "2030-09-20T10:00:00+00:00"} for i, t in enumerate(self.types)]
        return HttpResponse(200, json.dumps({"documents": docs}).encode())


def agent_with(sm, app_engine, required, documents):
    gateway = AppointmentServiceGateway("http://appointments.test", "k", transport=appointment(required),
                                        fallback=DocumentServiceGateway("http://docs.test", "k", transport=documents))
    patient = ScriptedAgents(sm, app_engine)
    return patient, Orchestrator(sm, FakeProvider(), gateway)


def test_a_missing_required_document_is_asked_for(sm, app_engine):
    patient, agent = agent_with(sm, app_engine, ["CBC", "COAGULATION_TESTS", "ECG"], Documents("CBC"))
    try:
        patient.submit(); patient.validate()
        _until(agent, patient, State.AWAITING_PATIENT_INPUT)
        case = sm.load(patient.case_id)
        assert case.required_documents == ["CBC", "COAGULATION_TESTS", "ECG"]
        assert case.held_documents == ["CBC"]
    finally:
        agent.close()


def test_everything_held_goes_straight_on(sm, app_engine):
    patient, agent = agent_with(sm, app_engine, ["CBC", "ECG"], Documents("ECG", "CBC", "URINALYSIS"))
    try:
        patient.submit(); patient.validate()
        _until(agent, patient, State.COMPLETED)
    finally:
        agent.close()


def test_an_appointment_that_needs_nothing_needs_no_upload(sm, app_engine):
    patient, agent = agent_with(sm, app_engine, [], Documents())
    try:
        patient.submit(); patient.validate()
        _until(agent, patient, State.COMPLETED)
    finally:
        agent.close()
```

(If `_until` has a different signature or `ScriptedAgents` needs arguments `validate()` does not take here, follow `tests/test_orchestrator.py`'s actual usage - read it first - and keep the three scenarios' assertions.)

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: `backend/hospital_agent/execution/document_service.py`** - built like `appointment_service.py` (read it): module docstring naming design §5.2; `TIMEOUT_SECONDS = 10.0`; `_TRANSIENT = {500: "unavailable", 502: "unavailable", 503: "unavailable", 504: "timeout"}`; `map_documents(response)`: transient statuses; 401/403 -> `unauthorized`; anything but 200 -> `invalid_response`; parse JSON (bounded body already); must be a dict with a list `documents`; every item a dict with a string `result` and a `document_type` that is `None` or a non-empty string (else `invalid_response`); `held = sorted({d["document_type"] for d in items if d["result"] == "ACCEPTED" and d["document_type"]})` -> `ToolResult(OK, {"held_documents": held})`. `DocumentServiceGateway.call`: non-`CheckDocuments` -> fallback; records `calls`; `patient_id` must be a non-empty string (else `ERROR invalid_request`, nothing sent); URL `f"{base}/api/v1/patients/{quote(patient_id, safe='')}/documents"`; headers `{"X-API-Key": key, "Accept": "application/json"}`; `transport` default `http.request`; `except (OSError, http.client.HTTPException) as exc: return no_answer_result(exc)`. `build_document_gateway(env=None, fallback=None)` mirrors `build_gateway` with `DOCUMENT_SERVICE_URL` / `DOCUMENT_API_KEY`, returning `fallback or MockGateway()` with `"mock"` when the URL is unset.

In `appointment_service.build_gateway`, add the `fallback` parameter: unset URL -> `(fallback if fallback is not None else MockGateway(), "mock")`; a configured service -> `AppointmentServiceGateway(url, key, fallback=fallback)`.

- [ ] **Step 4: Compose them in `api/app.py`.** Replace the single `build_gateway()` call with:

```python
                documents_gateway, documents_source = build_document_gateway()
                gateway, source = (None, documents_source) if documents_gateway is None \
                    else build_gateway(fallback=documents_gateway)
```

so a refused document configuration also keeps the Orchestrator stopped with its reason in `orchestrator`; on success set `app.state.appointments_source = source` and `app.state.documents_source = documents_source`, initialise `app.state.documents_source = None` next to `appointments_source`, and in `/health` add `extra["documents"] = request.app.state.documents_source` when it is not `None`. Update `tests/test_app_orchestrator.py`'s exact `/health` assertions to include `"documents": "mock"` (and add one test with `DOCUMENT_SERVICE_URL` set without a key -> `"orchestrator": "disabled: DOCUMENT_API_KEY is not set"`).

- [ ] **Step 5: Config and docs.** `docker-compose.yml` backend `environment`: `DOCUMENT_SERVICE_URL: ${DOCUMENT_SERVICE_URL:-}` and `DOCUMENT_API_KEY: ${DOCUMENT_API_KEY:-}` next to the appointment pair, with a comment. `tests/conftest.py`'s autouse fixture also deletes `DOCUMENT_SERVICE_URL` and `DOCUMENT_API_KEY`. `docs/api.md` `/health`: the `documents` field and the two new `disabled:` reasons.

- [ ] **Step 6: Full suite, golden (35/4/54), commit** `Read the patient's documents from the document-service for CheckDocuments`.

---

### Task 3: The patient's PDF upload through the Session Service

**Files:**
- Create: `backend/hospital_agent/document_intake.py`
- Modify: `backend/hospital_agent/session.py` (`upload_pdf`, `PatientView.document_upload`)
- Modify: `backend/hospital_agent/api/routes_patient.py` (new route), `backend/hospital_agent/api/schemas.py`, `backend/hospital_agent/api/app.py` (build the client; an upload-size middleware), `docs/api.md`
- Test: `backend/tests/test_document_intake.py`, `backend/tests/test_pdf_upload.py`

**Interfaces:**
- Produces: `document_intake.IntakeAnswer(result: str, document_id: str | None, document_type: str | None, duplicate_of: str | None)`; `document_intake.IntakeUnavailable(Exception)`; `document_intake.DocumentIntakeClient(base_url, api_key, *, transport=None, timeout=75.0)` with `.submit(patient_id: str, filename: str, data: bytes) -> IntakeAnswer`; `build_intake_client(env=None) -> DocumentIntakeClient | None`; `SessionService(..., document_intake=None)`; `SessionService.upload_pdf(patient_id, case_id, data, filename) -> UploadOutcome(code: str, document_type: str | None)` where `code` is one of `accepted`, `not_required`, `already_received`, `not_medical`, `unreadable`, `expired`, `not_yours`; exceptions `NotWaitingForDocument`, `IntakeUnavailable`; `PatientView.document_upload: "file" | "text"`; route `POST /api/patient/requests/{case_id}/documents/file`.

- [ ] **Step 1: The rules (write them into `session.upload_pdf`'s docstring).**
  1. The case must be the patient's (`CaseNotFound` -> 404) and in `AwaitingPatientInput` (else `NotWaitingForDocument` -> 409 `not_waiting_for_document`) - checked **before** anything is sent to the document-service.
  2. No intake client configured -> the route answers 404 `file_upload_not_enabled` (the text upload stays the way in).
  3. Forward `(patient_id, filename, data)`; `IntakeUnavailable` (no answer, 5xx, 401, or a body that is not the contract) -> 503 `document_service_unavailable`; nothing recorded.
  4. The effective type: `ACCEPTED` -> its `document_type`; `DUPLICATE_DOCUMENT` with `duplicate_of` and a `document_type` -> that type (a retry after a timeout is the document already delivered, design §4.1).
  5. With an effective type: if it is not in `case.required_documents` -> `not_required`, no event; if it is already in `case.held_documents` -> `already_received`, no event; otherwise call the existing `upload_document(patient_id, case_id, document_id=<the type>, content=<reference line>, fmt="pdf", document_extra={"document_ref": <DOC id>})` - the type is the `document_id` because `HOLD_DOCUMENT` appends `document_id` to `held_documents`; the reference line is exactly `f"{document_ref} {document_type} ACCEPTED"` (no medical text reaches the Data Log or the Safety re-check) - and return `accepted`.
  6. Without an effective type: `NON_MEDICAL_DOCUMENT` -> `not_medical`, `DOCUMENT_UNREADABLE` -> `unreadable`, `DOCUMENT_EXPIRED` -> `expired`, `PATIENT_MISMATCH` -> `not_yours`, any other result (including a duplicate without `duplicate_of`) -> `unreadable`. No event, nothing recorded.
  7. The application log gets only the outcome code - never the patient id, the file name or a document id.

- [ ] **Step 2: Failing tests.** `backend/tests/test_document_intake.py` - the client over a fake transport: builds a multipart body with one part named `file` (filename, `Content-Type: application/pdf`) and headers `X-API-Key`, `Content-Type: multipart/form-data; boundary=…`, `Accept`; POSTs to `…/api/v1/patients/<quoted id>/documents`; maps 201 bodies to `IntakeAnswer` (including `duplicate_of`); raises `IntakeUnavailable` on `OSError`, `HTTPException`, 5xx, 401/403, a non-JSON or non-contract body; the key never in `repr` or the exception text; timeout 75.0 (above the document-service's documented 70 s). `build_intake_client`: none without `DOCUMENT_SERVICE_URL`/`DOCUMENT_API_KEY` or with a non-http URL.

`backend/tests/test_pdf_upload.py` - through the real Session Service and State Manager with a fake intake client (a class with `submit` returning scripted `IntakeAnswer`s and recording calls), driving a case to `AwaitingPatientInput` the way `tests/test_session.py` does (read it; reuse its helpers). One test per rule of Step 1: accepted-and-required -> `DOCUMENT_UPLOADED` committed, state `Classifying`, `held_documents` gains the type, the Data Log's latest `uploaded_document` entry is exactly the reference line; accepted-but-not-required -> `not_required`, state and Data Log unchanged; already held -> `already_received`; a duplicate with `duplicate_of` of a required, not-held type -> `accepted`; each rejection code -> its outcome and no event; a case not waiting -> `NotWaitingForDocument` and the client never called; `IntakeUnavailable` propagates and nothing is recorded; caplog holds no patient id. And through the API (`TestClient` as in `tests/test_api_patient.py`): the route's status codes and body `{"upload": {"code": …, "document_type": …}, "request": <PatientCaseView>}`, 404 `file_upload_not_enabled` without a client, 409, 503, 413 for a body over 10 MB + 64 KiB (Content-Length) without the route running, and `PatientCaseView.document_upload` = `"file"` with a client, `"text"` without.

- [ ] **Step 3: Run to see them fail.**

- [ ] **Step 4: Implement.**
  - `document_intake.py`: standard library only (`http.request` from Task 1 for the transport, `uuid4().hex` as the boundary); `__repr__` without URL/key; `build_intake_client` reads the same two variables as `build_document_gateway`.
  - `session.py`: `SessionService.__init__(..., document_intake=None)`; `upload_pdf` per Step 1; `PatientView` gains `document_upload` (`"file"` if `self.document_intake` else `"text"`) set in `_view`.
  - `schemas.py`: `PatientCaseView.document_upload: Literal["file", "text"]`; `UploadResult(code: str, document_type: str | None)`; `PdfUploadResponse(upload: UploadResult, request: PatientCaseView)`.
  - `routes_patient.py`: `POST /requests/{case_id}/documents/file` with `file: UploadFile = File(...)`; read at most 10 MB + 1 bytes (`file.file.read(...)`; over -> 413 `too_large`); map exceptions to the codes of Step 1.
  - `app.py`: `SessionService(sm, wake=_wake, document_intake=build_intake_client() if owned else None)` (a test injects its own through the dependency override the existing API tests use - follow their pattern); a small pure-ASGI middleware, added in `create_app`, that for `POST` paths ending in `/documents/file` refuses a missing `Content-Length` (411 `length_required`) or one above `10 * 1024 * 1024 + 64 * 1024` (413 `too_large`) before the body is read, answering JSON `{"detail": code}` like the rest of the API.
  - `docs/api.md`: the new route (request, both response shapes, every status and code), `document_upload` in `PatientCaseView`, and that a PDF's content never enters the Data Log (a reference line does).

- [ ] **Step 5: Full suite, golden, commit** `Forward the patient's PDF to the document-service from the Session Service`.

---

### Task 4: The patient's upload screen and the document labels

**Files (frontend/):** `src/api/types.ts`, `src/api/client.ts` (+ `client.test.ts`), `src/pages/patient/helpers.ts` (+ test), `src/pages/patient/RequestDetail.tsx` (+ `RequestDetail.test.tsx`), `src/pages/staff/CaseMonitor.tsx` (+ test) or `src/pages/staff/labels.ts`, `src/pages/patient/fixtures.ts` if the view fixtures need `document_upload`.

- [ ] **Step 1: Types and client.** `PatientView.document_upload: 'file' | 'text'`; `UploadResult`, `PdfUploadResponse` exactly as `docs/api.md` (Task 3). `api.uploadDocumentFile(caseId, file: File): Promise<PdfUploadResponse>` sends `FormData` with field `file` (no JSON content type; the browser sets the multipart boundary) to `POST /patient/requests/{id}/documents/file`, through the same `request` helper's auth and error handling (extend it to accept a `FormData` body without setting `Content-Type`). Client test: the method, URL, `FormData` body with the file, the bearer token.

- [ ] **Step 2: Labels.** `documentLabel()` gains the five catalog codes with the design §2 Hebrew labels (`CBC` ספירת דם מלאה, `COAGULATION_TESTS` בדיקות קרישה, `ECG` תרשים פעילות חשמלית של הלב, `URINALYSIS` בדיקת שתן, `PREOP_SUMMARY` סיכום טרום ניתוח); the existing four stay. The staff Case Monitor's required/held facts show each code with its Hebrew label beside it (CLAUDE.md: label codes, never replace them).

- [ ] **Step 3: The upload screen.** In `RequestDetail`'s `MissingDocuments`: when `view.document_upload === 'file'`, render the missing list as today and **one** upload section ("העלאת מסמך PDF") with a file input `accept="application/pdf,.pdf"`, a hint (the system detects the document's type; PDF up to 10 MB), and a submit button; no text box, no format select. On submit: `api.uploadDocumentFile`, replace the view with `response.request`, and show a message per `response.upload.code` (with the detected type's label where there is one):
  - `accepted` - success: "המסמך {label} התקבל. הפנייה ממשיכה בטיפול."
  - `not_required` - info: "המסמך {label} תקין, אבל אינו נדרש לתור הזה."
  - `already_received` - info: "המסמך {label} כבר התקבל קודם."
  - `not_medical` - error: "הקובץ אינו מסמך רפואי, ולכן לא נקלט."
  - `unreadable` - error: "לא הצלחנו לקרוא את המסמך. ודאו שזה קובץ PDF ברור ונסו שוב."
  - `expired` - error: "המסמך {label} ישן מדי לפי כללי התוקף. יש להעלות מסמך עדכני."
  - `not_yours` - error: "המסמך אינו שייך לך, ולכן לא נקלט."
  - an unknown code - error, the neutral "המסמך לא נקלט. נסו שוב או פנו למוקד." (fail closed)
  - errors from the API (`409`, `413`, `503`, …) - through the existing `errorMessage()`, extended with Hebrew for `not_waiting_for_document`, `too_large`, `document_service_unavailable`, `file_upload_not_enabled`.
  A file that is not a PDF by name or type is refused in the browser before sending ("יש לבחור קובץ PDF."), as is one over 10 MB. When `document_upload === 'text'` the current per-document text forms stay exactly as they are.
- [ ] **Step 4: Tests** (`RequestDetail.test.tsx`): the file mode renders one PDF input and no text box; each of the seven codes shows its sentence; the view is replaced from `response.request`; a non-PDF and an oversize file are refused without calling the API; an API error shows the Hebrew sentence; the text mode is unchanged (existing tests pass). Never hit the network (stub the client as the existing tests do). `npm test` and `npm run build` pass.
- [ ] **Step 5: Commit** `Let the patient upload a PDF and see what became of it`.

---

### Task 5: The decisions, recorded

**Files:** `docs/spec_corrections.md` (rows 77-81, same single-row format as 72-76), `CLAUDE.md`, the design's status line.

- [ ] **Step 1: spec_corrections rows** - 77: requirements move to `CheckAppointment` (design §5.1), the mock with it, golden counts unchanged; an appointment-service without `required_documents` is `invalid_response` (fail closed). 78: `CheckDocuments` from the document-service listing (`held_documents` = types of results `ACCEPTED` as of today; only `patient_id` sent; §11 unchanged). 79: the Session Service forwards the patient's PDF - a second external call outside the Tool Executor, recorded as an exception: it is the patient's action, not a plan step, never proposed, allowed or retried; the Session Service already owns uploads (`DocumentValid` requires it). 80: an accepted upload's `document_id` in `DOCUMENT_UPLOADED` is the document **type** (so `HOLD_DOCUMENT` keeps `held_documents` a list of types), the document-service id travels as `document_ref`; the Data Log and the Safety re-check see only the reference line. 81: not-required, already-received and rejected uploads emit no event; a duplicate of an accepted original counts as that document (a retry after a timeout).
- [ ] **Step 2: CLAUDE.md** - Project status (sub-project 13 done: what the agent now reads and forwards), Commands (`DOCUMENT_SERVICE_URL` / `DOCUMENT_API_KEY`, `/health` `documents`, from the container `http://host.docker.internal:8090`), Working in the backend (`execution/http.py`, `execution/document_service.py`, `document_intake.py`, the Session Service's upload exception), the frontend section (the file mode of the upload screen), Verification targets' demo-stubs line.
- [ ] **Step 3: Commit** `Record sub-project 13's decisions`.
