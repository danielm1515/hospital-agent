# Sub-project 10 - CheckAppointment against the real appointment-service

**Status:** approved by the owner (2026-09-22), including the new API key for both systems.
**Scope:** outside the numbered spec work, like sub-project 9 - added at the owner's request
(`docs/spec_corrections.md` rows 72-75). Spec §18 makes the demo's external systems mocks;
this keeps them mocks everywhere except one action on the owner's live stack.

## 1. The gap

Every external system the Tool Executor calls is `MockGateway` (`execution/gateway.py`).
Its `CheckAppointment` answers "in 96 hours" for any patient, so an appointment the owner
books in the appointment-service (sub-project 9's companion project, `http://localhost:8080`)
never reaches a case. That service already has the endpoint the agent needs:

```
GET /api/v1/patients/{patient_id}/appointment      X-API-Key, X-Case-ID, X-Execution-ID
200 {"found": true,  "appointment": {..., "appointment_at": "...", ...}}
200 {"found": false, "appointment": null}
404 {"error": "patient_not_found"}          the registry does not know the patient
503 {"error": "patient_registry_unavailable" | "service_unavailable"}
504 {"error": "timeout"}                    (P-TIMEOUT, its failure-simulation hook)
401 {"error": "unauthorized"}
```

## 2. Decisions

1. **One action only.** `CheckAppointment` goes to the appointment-service; `CheckDocuments`,
   `LoadInstructions` and `SendStatusUpdate` stay on `MockGateway`, because no real system
   exists for them. Required documents stay `referral` + `blood_test` for every patient:
   documents per department needs a documents system, which the spec leaves to the vision
   document (אפיון מלא).
2. **Chosen by configuration, off by default.** `APPOINTMENT_SERVICE_URL` set -> the real
   `CheckAppointment`; unset or empty -> `MockGateway` exactly as today. The tests, the
   §15 golden traces (35 / 4 / 54) and the three §0 scenarios never set it, so they keep
   running on the mock and do not change.
3. **Fail closed at start.** URL set but `APPOINTMENT_API_KEY` empty -> the Agent Orchestrator
   does not start and `/health` says `disabled: APPOINTMENT_API_KEY is not set` (the same
   pattern as a missing OpenAI key). The key and the URL are never logged.
4. **A composite gateway.** `AppointmentServiceGateway(base_url, api_key, fallback=MockGateway())`
   implements `ToolGateway`: `CheckAppointment` is an HTTP call, every other action is the
   fallback's. `idempotent()` is the fallback's (every automatic action is idempotent,
   Execution design decision 3). The transport is the standard library (`urllib.request`),
   so the runtime dependencies do not change; it is injectable, so the tests need no network.
   No proxy is used, even when `HTTP_PROXY`/`http_proxy` is set in the environment
   (`urllib.request.ProxyHandler({})` on the opener) - a proxy would see `X-API-Key` and
   `patient_id`. The body is capped at 64 KiB: the transport never reads more than
   `MAX_BODY_BYTES` (65537) bytes of an answer, and a body at or over 64 KiB maps to
   `invalid_response` without being parsed as JSON.
5. **What the agent sends.** Only `patient_id` (spec §11 `minimized_fields`, already
   `ACTION_TARGETS[CheckAppointment]`), in the path, URL-quoted. Headers: `X-API-Key`, and
   `X-Execution-ID` = the call's `idempotency_key` (the service writes it into its audit,
   which joins the two systems' traces). `X-Case-ID` is not sent: `ToolGateway.call` does not
   receive the case id, and widening that interface is not worth it - the service generates
   its own. Timeout 5 s - `urllib`'s `timeout` bounds each socket operation (the connect, and
   each read) separately, not the call as a whole, so a server that answers in slow trickles
   could in principle take longer than 5 s end to end. Acceptable for the owner's local
   service; the bounded retry (§12.1 of the main spec) still limits the damage of a slow or
   wedged server.
6. **How each answer maps** (`ToolResult` kinds of `execution/gateway.py`; the executor and
   the Retry Manager are unchanged):

   | Answer | ToolResult | What the case does |
   |---|---|---|
   | 200, `found=true`, a timezone-aware `appointment_at`, `status="Scheduled"` and `appointment_at` still in the future | `ok {"appointment_at": <aware datetime>}` | `DATA_RETRIEVED`; readiness now uses the real time |
   | 200, `found=true`, but `status` is not `"Scheduled"` (including a missing `status`) or `appointment_at` is not strictly later than now | `error "not_found"` | escalates, same as `found=false` (row 76: a cancelled or past appointment must not be confirmed to the patient) |
   | 200, `found=false` | `error "not_found"` | escalates (`NonIdempotentFailure`) - resolve or reject only |
   | 404 `patient_not_found` | `error "patient_not_found"` | escalates, same |
   | 401 / 403 | `error "unauthorized"` | escalates, same |
   | 503, 504, 502, 500, a timeout, a refused connection | `transient_failure "timeout"` or `"unavailable"` | up to 3 attempts, then `RETRY_EXHAUSTED` (scenario 3's path) |
   | anything else, a body that is not the contract, a naive `appointment_at` | `error "invalid_response"` | escalates |

   The gateway never raises for an HTTP outcome; only a bug in it does, and the executor
   already turns that into `ExecutionUnknown`. `KNOWN_TOOL_ERRORS` gains `not_found`,
   `patient_not_found`, `unauthorized`, `unavailable` and `invalid_response`, so the Audit's
   `policy_reasons` record the code (it still holds codes only, never the service's message).
   Why `found=false` is an error and not an `ok` without a time: an `ok` would reach readiness
   with no `appointment_at`, which escalates as `Z3Counterexample` - a kind `HUMAN_APPROVED`
   can resume with a new deadline, into the same missing appointment. The escalation list is
   closed (§2.2), so the one kind that means "a call failed and a human decides" is used.
7. **The appointment-service returns a timezone.** It stores Israel local time without an
   offset (SQLite), so today `appointment_at` comes back naive, and the agent's F3 guard
   rejects a naive time. `AppointmentOut` attaches `Asia/Jerusalem` to a naive value; an aware
   one is left alone. (Changed in the owner's `appointment-service` project, outside this repo.)
8. **The patient's message shows Israel time.** `llm/message.py`'s template says `(UTC)` and
   prints the stored time as it is. A real appointment is local, so the template converts to
   `Asia/Jerusalem` and says `(שעון ישראל)`. The golden traces count rows, not text, so they
   are unchanged; the message's `content_hash` changes, which is expected.
9. **Networking.** The backend container reaches the service as
   `http://host.docker.internal:8080` (Docker Desktop), so `docker-compose.yml` gives the
   backend `extra_hosts: ["host.docker.internal:host-gateway"]` and passes
   `APPOINTMENT_SERVICE_URL` and `APPOINTMENT_API_KEY` through, both defaulting to empty.
10. **The key.** A new random key (`secrets.token_urlsafe(48)`) goes into both `.env` files:
    HospitalAgent's (new lines) and the appointment-service's (its `APPOINTMENT_API_KEY` line
    replaced in place, nothing else read or printed). The owner approved this. The old key
    stops working.

11. **`/health` says where appointments come from.** A real server adds
    `"appointments": "appointment-service"` or `"mock"` next to `"orchestrator"`, so the owner
    can see which one a running stack uses. Only the word - never the URL.

## 3. Components

- `backend/hospital_agent/execution/appointment_service.py` - `AppointmentServiceGateway`,
  the response mapping of §2.6, and `build_gateway(env) -> (ToolGateway | None, status)`
  (None + a status string when the configuration is refused, §2.3).
- `execution/gateway.py` - `KNOWN_TOOL_ERRORS` extended (§2.6).
- `api/app.py` - `build_gateway()` instead of `MockGateway()`; its status feeds `/health`.
- `llm/message.py` - Israel time (§2.8).
- `docker-compose.yml`, `CLAUDE.md`, `docs/spec_corrections.md` rows 72-75.
- appointment-service: `app/schemas.py` (§2.7) and its README.

## 4. Testing

- The mapping, row by row, through an injected transport: every line of §2.6, including a
  naive time, a missing field, a non-JSON body, and that only `patient_id` leaves.
- The URL-quoting of `patient_id`, the headers sent, the timeout, and that the key never
  appears in a `ToolResult`, an exception text or a log line.
- `build_gateway`: unset URL -> `MockGateway`; URL without key -> refused with its status;
  both -> the composite, whose other three actions answer exactly as `MockGateway`.
- End to end through the real Tool Executor and State Manager (the existing test harness,
  with the composite over a fake transport): a found appointment reaches `AwaitingPatientInput`
  with that `appointment_at`; `found=false` and 404 escalate `NonIdempotentFailure` with
  their reason code; 503 three times ends in `RETRY_EXHAUSTED`.
- One test over a real local HTTP server (`http.server` in a thread) for the `urllib` path.
- The template: a fixed UTC time renders in Israel time, in summer and in winter.
- The existing suite, the golden traces and the consistency proof unchanged.
- appointment-service: `appointment_at` comes back with `+03:00` / `+02:00`.
- Live check on the owner's stack after the merge: a booking made in the appointment-service
  shows up in a new case's message.
