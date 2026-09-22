# Hospital Agent

A hospital patient-service agent for *operational* requests - appointment status, required
documents, approved preparation instructions. It never answers a medical question by itself: a
question that turns out to be medical is escalated to clinical staff, who answer it themselves.

The design principle is **"the LLM proposes, deterministic layers decide"**. The model classifies
the request and proposes a plan; every step it proposes must pass layered formal checks before any
external call happens, and any unknown condition **fails closed** - the case stops and goes to a
human rather than continuing.

This is a final-project demo, built against a written specification (Hebrew). The specification is
the source of truth; decisions it leaves open are recorded in [`docs/spec_corrections.md`](docs/spec_corrections.md).

## How a case works

Each case is an event-driven state machine: **12 states, 26 events, 41 legal transitions**. Only the
State Manager writes state, and every transition is one Postgres transaction that also appends the
audit row. If no transition's guards hold, the event is blocked and the state does not change.

A single tool call goes through: the Planner proposes an action -> the State Manager checks it is in
the plan and the plan is unchanged -> the Policy Service decides -> the Tool Executor re-verifies
everything it was told -> the call runs outside the transaction -> the result comes back as an event.
An execution whose outcome is unknown after a restart is never replayed automatically.

**The Policy Service is five engines that must agree:**

| Engine | What it decides |
|---|---|
| OPA (Rego) | Allow / RequireHumanReview / Deny, default Deny |
| Prolog | Role and action authorization, with explanations |
| Datalog | Which sensitive fields may flow to which system |
| Z3 | Whether there is time to ask the patient for a document, and cross-layer consistency proofs |
| Temporal Monitor | Past-time rules over the case's own audit trace, checked before every commit |

A disagreement, or an engine that cannot be reached, means Deny.

**Three separate logs:** the Audit is append-only and holds codes and hashes only; the Data Log holds
medical content and can be deleted, leaving a tombstone; the application log never holds a patient id,
request content or a secret.

## The three systems

This repository is the agent. It talks to two standalone services, each in its own repository:

| Repository | What it is |
|---|---|
| **Hospital Agent** (this one) | The agent, its API and the patient and staff screens |
| **appointment-service** | Booking and editing appointments, and the lookup the agent reads |
| **document-service** | The patient's PDFs: intake checks, LLM classification, private S3 storage |

The agent runs against mocks by default, so it needs neither service to work. Point it at the real
ones with `APPOINTMENT_SERVICE_URL` / `APPOINTMENT_API_KEY` and `DOCUMENT_SERVICE_URL` /
`DOCUMENT_API_KEY`; a URL set without its key is refused rather than ignored.

## Running it

```bash
docker compose up --build
```

The API is on `localhost:8000`, the UI on `localhost:5273`, Postgres on `localhost:54322`, and the
migrations run at start. The LLM needs `OPENAI_API_KEY` in `.env` (git-ignored); without it the
server runs but the agent does not start, and `/health` says so.

```bash
docker compose run --rm backend pytest        # the backend suite
cd frontend && npm install && npm test        # the UI suite
```

The three specified scenarios, replayed end to end from the running system, print their audit row
counts (35 / 4 / 54):

```bash
docker compose run --rm backend python -m obs.golden
```

The cross-layer consistency proof:

```bash
docker compose run --rm backend python -m hospital_agent.policy.consistency
```

## Layout

- `backend/hospital_agent/` - the components: the state machine and its guards, the policy engines,
  the Tool Executor, the four LLM calls, the Session Service and the human review service.
- `backend/tests/` - the acceptance suite, named after the specification's own test numbers.
- `frontend/` - the patient screen and the staff screen (React, Hebrew, right-to-left).
- `docs/` - the API contract, the specification in Markdown, and the recorded decisions.

## Stack

Python 3.13 + FastAPI, PostgreSQL, React + TypeScript, OPA 1.9.0, Z3 4.15.4, and OpenAI's
`gpt-5.6-luna` behind strict JSON schemas. Everything runs in Docker.
