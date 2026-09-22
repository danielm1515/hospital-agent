# Sub-project 9 (Patients Registry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the project a `patients` table other systems can read, and make the user's `appointment-service` check every patient against it.

**Architecture:** Migration 0004 creates `patients` (id, name, E.164 phone), seeds the three demo patients and creates a read-only `hospital_reader` role with `SELECT` on that one table. The appointment-service gains a small `PatientRegistry` port with a Postgres implementation; when `PATIENT_REGISTRY_URL` is set, its appointment endpoint refuses an unknown patient with `404` and fails closed with `503` when the registry is unreachable. The demo ids are aligned across both systems.

**Tech Stack:** HospitalAgent — Python 3.13, SQLAlchemy Core, Alembic, pytest, Postgres 16. appointment-service — Python 3.12, FastAPI, SQLAlchemy 2.0, SQLite, pytest; adds `psycopg[binary]==3.3.6` (the version HospitalAgent's `uv.lock` already resolves).

**Spec:** `docs/superpowers/specs/2026-09-22-patients-registry-design.md`; binding spec `docs/spec/18-operational-assumptions.md` §18.2-§18.3, `docs/spec/12-context-audit-approval.md` §12.3.

## Global Constraints

- **Two projects, two locations.** HospitalAgent is `G:/HospitalAgent` (a git repo; work in the worktree you are given). The appointment-service is `C:/Users/danie/Documents/ChatGPT/לימודים פרויקט גמר/appointment-service` — **it has no git history**, so nothing there can be undone except from the backup Task 3 makes first.
- **Never read, print, copy or modify either project's `.env`.** It holds keys.
- **Never delete user data.** The appointment-service's SQLite database lives in the Docker volume `appointment_sqlite_data` and may hold appointments the user entered through its admin UI: it is never reset or recreated.
- **Never disturb the user's running stacks** (`hospital-agent-*` on 54322 / 8200 / 5273, `appointment-service-api-1` on 8080) except in Task 4, which the controller runs.
- **Nothing in HospitalAgent's binding lists changes:** 12 States, 26 Events, the 41 rows of the §3 table, the 12 temporal rules, the closed Action list. The §18.3 IdP (`DEMO_USERS` in `auth.py`) is unchanged.
- **The patients table holds personal data** (name, phone): the Application Log never contains either (§12.3), and `hospital_reader` can read that one table and nothing else.
- HospitalAgent runs its suite in Docker: `docker compose -p <project> -f docker-compose.yml -f <no-ports override> run --rm backend pytest -q`. Every new HospitalAgent migration `GRANT`s what it creates.
- LF line endings. Every HospitalAgent commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. The appointment-service is never committed to (its repo is the user's and has no commits).

## File Structure

| File | Project | Responsibility |
|---|---|---|
| `backend/alembic/versions/0004_patients.py` | HospitalAgent | The table, the seed, the reader role, the grants |
| `backend/hospital_agent/db.py` | HospitalAgent | Mirrors the new table |
| `docker-compose.yml` | HospitalAgent | Passes `READER_DB_PASSWORD` to the backend |
| `backend/tests/test_patients.py` | HospitalAgent | Seed vs `DEMO_USERS`, role permissions, the phone check, re-running the migration |
| `docs/patients-registry.md` | HospitalAgent | The contract an external system reads |
| `CLAUDE.md`, `docs/spec_corrections.md` | HospitalAgent | What was built, and the spec decisions |
| `app/patient_registry.py` | appointment-service | The `PatientRegistry` port and its Postgres implementation |
| `app/config.py`, `app/main.py` | appointment-service | The setting, the wiring, the check in the endpoint, the seed |
| `requirements.txt`, `compose.yaml` | appointment-service | The driver, and the registry URL for the normal run |
| `tests/test_patient_registry.py` | appointment-service | Every outcome of the check |

---

### Task 1: The `patients` table in HospitalAgent

**Files:**
- Create: `backend/alembic/versions/0004_patients.py`, `backend/tests/test_patients.py`, `docs/patients-registry.md`
- Modify: `backend/hospital_agent/db.py`, `docker-compose.yml`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: table `patients(patient_id text PK, full_name text NOT NULL, phone text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())` seeded with `P-10041`, `P-20000`, `P-30000`; role `hospital_reader` (LOGIN, password from `READER_DB_PASSWORD`, default `hospital_reader_dev`) with `SELECT` on `patients` only; `hospital_app` with `SELECT` on `patients`. Task 3's `PostgresPatientRegistry` queries `SELECT 1 FROM patients WHERE patient_id = :patient_id` as `hospital_reader`.

**Context the implementer needs:**

- Migrations run as `hospital_owner`, a superuser, so a migration may create a role. `db/init/01-init.sh` creates `hospital_app` but runs **only when the volume is first created**, so it will never run on the user's existing database — that is why the role is created in the migration.
- Roles are cluster-wide, and the same migration runs on both `hospital` and `hospital_test`, so creating the role must be idempotent. The test fixture `migrated` (in `tests/conftest.py`) runs `downgrade base` then `upgrade head` once per session, so 0004's `upgrade()` will routinely run with the role already present.
- `tests/conftest.py`'s `app_engine` fixture TRUNCATEs `data_log, audit_log, approvals, executions, cases` between tests. **Do not add `patients` to that list** — the seed must survive every test.
- `tests/test_schema.py::test_tables_and_columns_match_db_py` iterates `db.metadata.sorted_tables`, so adding the table to `db.py` is what makes that test cover it.

- [ ] **Step 1: Pass the reader password to the backend**

In `docker-compose.yml`, in the `backend` service's `environment:`, directly after the `DEMO_PASSWORD` line:

```yaml
      # Sub-project 9: the read-only role other systems use for the patients table.
      READER_DB_PASSWORD: ${READER_DB_PASSWORD:-hospital_reader_dev}
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_patients.py`:

```python
"""Sub-project 9: the patients registry other systems read (design §3)."""
import os

import pytest
from alembic import command
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from hospital_agent.auth import DEMO_USERS, PATIENT

READER = "hospital_reader"
OTHER_TABLES = ["cases", "audit_log", "data_log", "approvals", "executions"]


def reader_password() -> str:
    return os.environ.get("READER_DB_PASSWORD", "hospital_reader_dev")


def engine_as(role: str, password: str):
    url = make_url(os.environ["TEST_DATABASE_URL"]).set(username=role, password=password)
    return create_engine(url)


@pytest.fixture
def reader(migrated):
    engine = engine_as(READER, reader_password())
    yield engine
    engine.dispose()


def test_the_registry_holds_exactly_the_idps_patients(app_engine):
    """DEMO_USERS stays the IdP (§18.3); the table must never drift from it (design §3.3)."""
    with app_engine.connect() as conn:
        rows = {(r.patient_id, r.full_name) for r in conn.execute(text("SELECT patient_id, full_name FROM patients"))}
    expected = {(u.user_id, u.display_name) for u in DEMO_USERS.values() if u.role == PATIENT}
    assert rows == expected


def test_every_phone_is_e164(app_engine):
    with app_engine.connect() as conn:
        phones = [r.phone for r in conn.execute(text("SELECT phone FROM patients"))]
    assert phones and all(p.startswith("+") and p[1:].isdigit() and 8 <= len(p) - 1 <= 15 for p in phones)


def test_the_database_rejects_a_phone_that_is_not_e164(owner_engine, migrated):
    with pytest.raises(IntegrityError, match="ck_patients_phone_e164"):
        with owner_engine.begin() as conn:
            conn.execute(text("INSERT INTO patients (patient_id, full_name, phone) "
                              "VALUES ('P-BADPHONE', 'בדיקה', '050-1234567')"))


def test_the_reader_can_read_the_patients(reader):
    with reader.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3


@pytest.mark.parametrize("table", OTHER_TABLES)
def test_the_reader_cannot_read_any_other_table(reader, table):
    with pytest.raises(ProgrammingError, match="permission denied"):
        with reader.connect() as conn:
            conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))


@pytest.mark.parametrize("statement", [
    "INSERT INTO patients (patient_id, full_name, phone) VALUES ('P-X', 'x', '+972500000099')",
    "UPDATE patients SET full_name = 'x'",
    "DELETE FROM patients",
    "TRUNCATE patients",
])
def test_the_reader_cannot_change_the_patients(reader, statement):
    with pytest.raises(ProgrammingError, match="permission denied"):
        with reader.begin() as conn:
            conn.execute(text(statement))


def test_the_app_role_reads_the_patients_but_cannot_write_them(app_engine):
    with app_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3
    with pytest.raises(ProgrammingError, match="permission denied"):
        with app_engine.begin() as conn:
            conn.execute(text("UPDATE patients SET full_name = 'x'"))


def test_rerunning_the_migration_keeps_an_existing_roles_password(owner_engine, migrated, alembic_config):
    """Roles are cluster-wide: 0004 must not fail on an existing role, nor reset a password
    the owner of the database changed (design §3.2)."""
    changed = "changed-by-the-owner"
    with owner_engine.begin() as conn:
        conn.execute(text(f"ALTER ROLE {READER} PASSWORD '{changed}'"))
    try:
        command.downgrade(alembic_config, "0003")
        command.upgrade(alembic_config, "head")
        engine = engine_as(READER, changed)
        try:
            with engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3
        finally:
            engine.dispose()
    finally:
        with owner_engine.begin() as conn:
            conn.execute(text(f"ALTER ROLE {READER} PASSWORD '{reader_password()}'"))
```

The last test needs the Alembic config the `migrated` fixture builds. In `tests/conftest.py`, extract it into a fixture and have `migrated` use it — change nothing else in that file:

```python
@pytest.fixture(scope="session")
def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["url"] = _env("TEST_MIGRATION_DATABASE_URL")
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def migrated(owner_engine: Engine, alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
```

- [ ] **Step 3: Run them and watch them fail**

Run: `docker compose -p <project> -f docker-compose.yml -f <override> run --rm backend pytest tests/test_patients.py -v`
Expected: every test fails with `relation "patients" does not exist` (or, for the reader, a login failure — the role does not exist yet).

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/0004_patients.py`:

```python
"""The patients registry (sub-project 9) - a table other systems read.

The §18.3 IdP stays the fixed user list in auth.py; this table is the registry the outside
world reads, and tests/test_patients.py fails if the two ever disagree. Names and phones are
personal data (§12.3): only hospital_reader, a role with SELECT on this one table, is given
to other systems.

db/init/01-init.sh runs only when the volume is first created, so the reader role is created
here, idempotently: roles are cluster-wide, and this migration runs on both hospital and
hospital_test. An existing role keeps its password.

Revision ID: 0004
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "hospital_app"
READER_ROLE = "hospital_reader"

# The same three patients as auth.DEMO_USERS. The phones are placeholders the owner replaces.
PATIENTS = [
    {"patient_id": "P-10041", "full_name": "דנה כהן", "phone": "+972500000001"},
    {"patient_id": "P-20000", "full_name": "יוסי לוי", "phone": "+972500000002"},
    {"patient_id": "P-30000", "full_name": "מיכל אברהם", "phone": "+972500000003"},
]


def upgrade() -> None:
    patients = op.create_table(
        "patients",
        sa.Column("patient_id", sa.Text, primary_key=True),
        sa.Column("full_name", sa.Text, nullable=False),
        sa.Column("phone", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(r"phone ~ '^\+[1-9][0-9]{7,14}$'", name="ck_patients_phone_e164"),
    )
    op.bulk_insert(patients, PATIENTS)

    # A dev default, like APP_DB_PASSWORD. Quotes are doubled so the literal stays one literal.
    password = os.environ.get("READER_DB_PASSWORD", "hospital_reader_dev").replace("'", "''")
    op.execute(
        f"""
        DO $body$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{READER_ROLE}') THEN
                CREATE ROLE {READER_ROLE} LOGIN PASSWORD '{password}';
            END IF;
        END
        $body$
        """
    )
    op.execute(f"GRANT SELECT ON patients TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON patients TO {READER_ROLE}")


def downgrade() -> None:
    # The grants go with the table. The role stays: it is cluster-wide and may hold grants in
    # the other database, where dropping it would fail.
    op.drop_table("patients")
```

- [ ] **Step 5: Mirror the table in `db.py`**

In `backend/hospital_agent/db.py`, after the `data_log` table, and extend the module docstring's first line to say "plus the §12.3 Data Log and the sub-project 9 patients registry":

```python
patients = Table(
    "patients",
    metadata,
    Column("patient_id", Text, primary_key=True),
    Column("full_name", Text, nullable=False),
    Column("phone", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
```

- [ ] **Step 6: Run the tests**

Run: `... run --rm backend pytest tests/test_patients.py tests/test_schema.py -v`
Expected: PASS.

- [ ] **Step 7: Write the contract other systems read**

Create `docs/patients-registry.md`:

````markdown
# Patients registry - the contract for other systems

The one table in this project another system may read. `docs/api.md` is the contract for the
UI; this file is the contract for everything else.

## Connecting

| | From the host | From another Docker container |
|---|---|---|
| Host | `127.0.0.1` | `host.docker.internal` |
| Port | `54322` | `54322` |
| Database | `hospital` | `hospital` |
| User | `hospital_reader` | `hospital_reader` |
| Password | `READER_DB_PASSWORD` (default `hospital_reader_dev`) | same |

Use `127.0.0.1`, not `localhost`: on this machine `localhost` resolves to `::1` first, and the
port is published on IPv4 only. A container on Linux needs
`extra_hosts: ["host.docker.internal:host-gateway"]`; Docker Desktop resolves the name itself.

```
postgresql://hospital_reader:hospital_reader_dev@127.0.0.1:54322/hospital
```

SQLAlchemy with psycopg 3 wants the driver in the scheme: `postgresql+psycopg://...`.

## The table

```sql
patients(
    patient_id  text        PRIMARY KEY,   -- 'P-10041'
    full_name   text        NOT NULL,      -- Hebrew
    phone       text        NOT NULL,      -- E.164, e.g. '+972501234567'
    created_at  timestamptz NOT NULL DEFAULT now()
)
```

`phone` is always E.164 - a `+`, a country code, and 8 to 15 digits in all, no spaces or
dashes. The database rejects anything else (`CHECK ck_patients_phone_e164`).

## What `hospital_reader` may do

- `SELECT` on `patients`. Nothing else.
- It cannot read `cases`, `audit_log`, `data_log`, `approvals` or `executions` - that is where
  the agent keeps the medical content and the audit trace (§12.3).
- It cannot insert, update or delete anything.

Read only the columns you need. A check that a patient exists needs
`SELECT 1 FROM patients WHERE patient_id = $1`, not the name and phone. Never write a name or a
phone into an application log.

## Adding a patient

The patients also exist in the demo IdP, `DEMO_USERS` in `backend/hospital_agent/auth.py`, and
`backend/tests/test_patients.py` fails if the two disagree. So a new patient means a new
migration that inserts the row, **and** the same patient in `DEMO_USERS`.
````

- [ ] **Step 8: Run the whole suite and the golden traces**

Run: `... run --rm backend pytest -q` → 0 failures (645 passed / 1 skipped before this task, plus the new tests).
Run: `... run --rm backend python -m obs.golden` → `35`, `4`, `54`.

- [ ] **Step 9: Commit**

```bash
git add backend/alembic/versions/0004_patients.py backend/hospital_agent/db.py backend/tests/test_patients.py backend/tests/conftest.py docker-compose.yml docs/patients-registry.md
git commit -m "Add the patients registry and a read-only role for other systems"
```

---

### Task 2: HospitalAgent documentation

**Files:**
- Modify: `CLAUDE.md`, `docs/spec_corrections.md`

**Interfaces:**
- Consumes: what Task 1 built, and the appointment-service behaviour Task 3 will build (design §4).
- Produces: nothing code depends on.

- [ ] **Step 1: `CLAUDE.md`**

- *Project status:* sub-projects 1-9; name 9 as "the patients registry: a `patients` table other systems read through the read-only `hospital_reader` role, and the appointment-service checking every patient against it".
- *Tech stack → Database:* the four-table model, plus the Data Log, plus `patients` (`docs/spec_corrections.md` rows 65-68).
- *Working in the backend*, beside the `db.py` bullet: `patients` is seeded by migration 0004 and mirrors `DEMO_USERS`; `tests/test_patients.py` fails if they drift; `hospital_reader` has `SELECT` on `patients` only; the contract for other systems is `docs/patients-registry.md`; the `app_engine` fixture deliberately does not TRUNCATE `patients`.
- *Commands:* `READER_DB_PASSWORD` (default `hospital_reader_dev`) sets the reader's password when the role is first created.

Change only what is now incomplete or wrong. Read the whole file first, and afterwards check that no sentence in it contradicts another — a previous documentation task in this repo missed that twice.

- [ ] **Step 2: `docs/spec_corrections.md`**

Append, continuing the numbering (the file ends at row 64), in the file's three-column shape:

```markdown
| 65 | §18.2 defines four tables (five with the Data Log, row 29). The owner needs the patients in the database, for another system to read. Where do they go? | A sixth table, `patients` (id, full name, E.164 phone), added at the owner's explicit request rather than from the spec. It is seeded with the three `DEMO_USERS` patients by migration 0004. It is a registry for other systems, not a new source of identity: nothing in the agent's flow reads it. | `backend/alembic/versions/0004_patients.py`, `docs/patients-registry.md` |
| 66 | Should the login read the patients from that table, so there is one source of truth? | No. §18.3 binds the IdP to a fixed user list, and that list also carries the demo's identity verdict (`P-30000` fails verification on purpose) - an IdP concept, not a patient attribute another system should see. `DEMO_USERS` stays the IdP, and `tests/test_patients.py` fails if the table and the list ever disagree on ids or names. | `backend/hospital_agent/auth.py`, `backend/tests/test_patients.py` |
| 67 | How does another system read the table without the application's rights? | Through `hospital_reader`, a role with `SELECT` on `patients` and nothing else: no other table, no write. It is created by the migration itself, idempotently, because `db/init/` runs only on a new volume and roles are cluster-wide; an existing role keeps its password. | `backend/alembic/versions/0004_patients.py`, `backend/tests/test_patients.py` |
| 68 | Should `cases.patient_id` reference `patients`? | No. The tests create cases for `P-1`, `P-2` and `P-OTHER`, so a foreign key would break dozens of them, and it would add no safety: a case opens only for a patient whose verified token comes from `DEMO_USERS`, and row 66's test guarantees every one of them is in the table. | `docs/superpowers/specs/2026-09-22-patients-registry-design.md` §3.4 |
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/spec_corrections.md
git commit -m "Document the patients registry and record its spec decisions"
```

---

### Task 3: The patient check in appointment-service

**Location:** `C:/Users/danie/Documents/ChatGPT/לימודים פרויקט גמר/appointment-service` (below, `AS/`). **Not a git worktree: you are editing the user's project in place.**

**Files:**
- Create: `AS/app/patient_registry.py`, `AS/tests/test_patient_registry.py`
- Modify: `AS/app/config.py`, `AS/app/main.py`, `AS/requirements.txt`, `AS/compose.yaml`

**Interfaces:**
- Consumes: Task 1's table and role (`SELECT 1 FROM patients WHERE patient_id = :patient_id` as `hospital_reader`).
- Produces: `create_app(..., patient_registry: PatientRegistry | None = None)`; `GET /api/v1/patients/{id}/appointment` answering `404 {"error": "patient_not_found"}` and `503 {"error": "patient_registry_unavailable"}` when a registry is configured.

- [ ] **Step 1: Back up the folder — before anything else**

The project has no git history. From the parent folder `לימודים פרויקט גמר`, copy `appointment-service` to `appointment-service.backup-2026-09-22`, excluding `.env` and `.pytest_cache`:

```bash
cd "/c/Users/danie/Documents/ChatGPT/לימודים פרויקט גמר"
test -e appointment-service.backup-2026-09-22 && { echo "backup already exists - stop and report"; exit 1; }
mkdir appointment-service.backup-2026-09-22
tar -C appointment-service --exclude=.env --exclude=.pytest_cache -cf - . | tar -C appointment-service.backup-2026-09-22 -xf -
diff -rq --exclude=.env --exclude=.pytest_cache appointment-service appointment-service.backup-2026-09-22 && echo "backup verified"
```

If a backup already exists, **stop and report** rather than overwriting it. Do not read `.env`, and do not proceed until the command prints `backup verified`.

- [ ] **Step 2: Record the baseline**

Run the service's existing tests in a throwaway container (the service's own image lacks `pytest`):

```bash
cd "/c/Users/danie/Documents/ChatGPT/לימודים פרויקט גמר/appointment-service"
docker run --rm -v "$(pwd -W):/src" -w /src python:3.12-slim sh -c "pip install -q -r requirements-dev.txt && pytest -q"
```

Expected: every existing test passes. If any fails before you change a line, stop and report it — do not fix pre-existing failures.

- [ ] **Step 3: Write the failing tests**

Create `AS/tests/test_patient_registry.py`:

```python
"""Sub-project 9: every patient is checked against HospitalAgent's registry (design §4.3)."""
import os

os.environ["ENABLE_FAILURE_SIMULATION"] = "true"
os.environ["MOCK_TIMEOUT_PATIENT_ID"] = "P-TIMEOUT"

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import Appointment, AuditLog
from app.patient_registry import RegistryUnavailable


class FakeRegistry:
    def __init__(self, known: set[str] | None = None, broken: bool = False) -> None:
        self.known = known or set()
        self.broken = broken
        self.asked: list[str] = []

    def exists(self, patient_id: str) -> bool:
        self.asked.append(patient_id)
        if self.broken:
            raise RegistryUnavailable("down")
        return patient_id in self.known


def make_client(tmp_path, registry):
    app = create_app(f"sqlite:///{(tmp_path / 'test.db').as_posix()}", seed_demo_data=True,
                     patient_registry=registry)
    return app, TestClient(app)


def last_audit_result(app) -> str:
    with app.state.SessionLocal() as session:
        return session.scalars(select(AuditLog.result).order_by(AuditLog.timestamp.desc())).first()


def test_a_known_patient_with_an_appointment_gets_it(tmp_path):
    app, client = make_client(tmp_path, FakeRegistry({"P-10041"}))
    with client:
        response = client.get("/api/v1/patients/P-10041/appointment")
    assert response.status_code == 200
    assert response.json()["appointment"]["appointment_id"] == "APT-8391"


def test_a_known_patient_without_an_appointment_is_still_a_business_result(tmp_path):
    app, client = make_client(tmp_path, FakeRegistry({"P-30000"}))
    with client:
        response = client.get("/api/v1/patients/P-30000/appointment")
    assert response.status_code == 200
    assert response.json() == {"found": False, "appointment": None}


def test_a_patient_the_registry_does_not_know_is_404(tmp_path):
    app, client = make_client(tmp_path, FakeRegistry({"P-10041"}))
    with client:
        response = client.get("/api/v1/patients/P-99999/appointment",
                              headers={"X-Case-ID": "CASE-9", "X-Execution-ID": "EXEC-9"})
        result = last_audit_result(app)
    assert response.status_code == 404
    assert response.json()["error"] == "patient_not_found"
    assert response.headers["x-case-id"] == "CASE-9"
    assert result == "patient_not_found"


def test_an_unreachable_registry_fails_closed(tmp_path):
    """§14: an appointment for a patient nobody could verify is never returned."""
    app, client = make_client(tmp_path, FakeRegistry(broken=True))
    with client:
        response = client.get("/api/v1/patients/P-10041/appointment")
        result = last_audit_result(app)
    assert response.status_code == 503
    assert response.json()["error"] == "patient_registry_unavailable"
    assert "appointment" not in response.json()
    assert result == "technical_failure"


def test_the_timeout_hook_runs_before_the_registry(tmp_path):
    """P-TIMEOUT is a test hook, not a patient: it must still produce its 504 (design §4.3)."""
    registry = FakeRegistry(set())
    app, client = make_client(tmp_path, registry)
    with client:
        response = client.get("/api/v1/patients/P-TIMEOUT/appointment")
    assert response.status_code == 504
    assert registry.asked == []


def test_without_a_registry_the_service_behaves_as_before(tmp_path):
    app, client = make_client(tmp_path, None)
    with client:
        response = client.get("/api/v1/patients/P-99999/appointment")
    assert response.status_code == 200
    assert response.json() == {"found": False, "appointment": None}


def test_the_demo_seed_uses_hospital_agents_patient_ids(tmp_path):
    app, client = make_client(tmp_path, None)
    with client:
        with app.state.SessionLocal() as session:
            owners = dict(session.execute(select(Appointment.appointment_id, Appointment.patient_id)).all())
    assert owners == {"APT-8391": "P-10041", "APT-8392": "P-20000"}
```

- [ ] **Step 4: Run them and watch them fail**

Run the Step 2 command. Expected: the new file fails at import (`No module named 'app.patient_registry'`); the existing tests still pass.

- [ ] **Step 5: The registry port**

Create `AS/app/patient_registry.py`:

```python
"""HospitalAgent's patients registry, as this service sees it (sub-project 9).

The registry is the `patients` table in HospitalAgent's Postgres, read as hospital_reader - a
role that can read that one table and nothing else. This service only ever asks whether a
patient exists, so it never reads a name or a phone.
"""
from typing import Protocol

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


class RegistryUnavailable(Exception):
    """The registry could not be asked. The caller must fail closed."""


class PatientRegistry(Protocol):
    def exists(self, patient_id: str) -> bool:
        """True if the patient is registered. Raises RegistryUnavailable if it cannot tell."""


class PostgresPatientRegistry:
    def __init__(self, url: str) -> None:
        self._engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})

    def exists(self, patient_id: str) -> bool:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text("SELECT 1 FROM patients WHERE patient_id = :patient_id"),
                    {"patient_id": patient_id},
                ).first()
        except SQLAlchemyError as exc:
            raise RegistryUnavailable(type(exc).__name__) from exc
        return row is not None

    def dispose(self) -> None:
        self._engine.dispose()
```

- [ ] **Step 6: The setting and the driver**

In `AS/app/config.py`, as the last field of `Settings`:

```python
    patient_registry_url: str = os.getenv("PATIENT_REGISTRY_URL", "")
```

In `AS/requirements.txt`, append:

```
psycopg[binary]==3.3.6
```

- [ ] **Step 7: Wire it into `create_app` and the endpoint**

In `AS/app/main.py`:

1. Import: `from .patient_registry import PatientRegistry, PostgresPatientRegistry, RegistryUnavailable`.
2. `create_app` gains a last keyword parameter `patient_registry: PatientRegistry | None = None`. After `api_key_value` is computed:

```python
    registry = patient_registry
    if registry is None and settings.patient_registry_url:
        registry = PostgresPatientRegistry(settings.patient_registry_url)
    # Once, at startup, so there is never doubt which mode the service runs in. The URL itself
    # is never logged: it carries a password.
    logger.info("patient registry check: %s", "enabled" if registry is not None else "disabled")
```

3. In `lifespan`, after `engine.dispose()`, dispose the registry if it has a `dispose`:

```python
        if hasattr(registry, "dispose"):
            registry.dispose()
```

4. After `app.state.settings = settings`: `app.state.patient_registry = registry`.
5. In the `@app.get("/api/v1/patients/{patient_id}/appointment", ...)` decorator's `responses=`, add `404: {"model": ErrorResult},`.
6. In `check_appointment`, **after** the `raise SimulatedTimeout` block and **before** the `appointment = session.scalar(...)` query, insert:

```python
                registry = request.app.state.patient_registry
                if registry is not None:
                    try:
                        known = registry.exists(patient_id)
                    except RegistryUnavailable:
                        logger.warning("patient registry unavailable")
                        _write_audit(
                            session,
                            case_id=case_id,
                            execution_id=execution_id,
                            patient_id=patient_id,
                            result="technical_failure",
                            latency_ms=round((time.perf_counter() - started) * 1000),
                        )
                        return JSONResponse(
                            status_code=503,
                            content={"error": "patient_registry_unavailable",
                                     "message": "The patient registry could not be reached"},
                            headers={"X-Case-ID": case_id, "X-Execution-ID": execution_id},
                        )
                    if not known:
                        _write_audit(
                            session,
                            case_id=case_id,
                            execution_id=execution_id,
                            patient_id=patient_id,
                            result="patient_not_found",
                            latency_ms=round((time.perf_counter() - started) * 1000),
                        )
                        return JSONResponse(
                            status_code=404,
                            content={"error": "patient_not_found",
                                     "message": "The patient is not in the registry"},
                            headers={"X-Case-ID": case_id, "X-Execution-ID": execution_id},
                        )
```

The warning names no patient: the id, name and phone never enter the application log.

- [ ] **Step 8: Align the demo seed**

In `_seed()`, change `APT-8392`'s `patient_id` from `"P-10042"` to `"P-20000"`. Change nothing else there — this is the code path for a **new** database only; the running one is fixed in place in Task 4.

- [ ] **Step 9: Run the tests**

Run the Step 2 command. Expected: every test passes — the existing ones unchanged, the new ones green.

- [ ] **Step 10: Configure the normal run**

In `AS/compose.yaml`, in `services.api.environment`, add:

```yaml
      # Sub-project 9: check every patient against HospitalAgent's registry (read-only role).
      PATIENT_REGISTRY_URL: ${PATIENT_REGISTRY_URL:-postgresql+psycopg://hospital_reader:hospital_reader_dev@host.docker.internal:54322/hospital}
```

and under `services.api`, beside `ports:`:

```yaml
    # Docker Desktop resolves host.docker.internal itself; Linux needs this mapping.
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Do **not** rebuild or restart the running container — Task 4 does that.

- [ ] **Step 11: Report**

There is nothing to commit in this project. In your report, list every file you changed with a one-line reason, the baseline and final test output, and the backup's path.

---

### Task 4: Bring it live (the controller runs this, not a subagent)

This task changes the user's running systems, so the controller does it after Tasks 1-3 are reviewed and HospitalAgent is merged.

- [ ] **Step 1: Migrate HospitalAgent's live database**

`docker compose up -d` from `G:/HospitalAgent` (with `BACKEND_HOST_PORT=8200` while Windows still reserves 8000). The backend runs migrations on start, so 0004 runs on `hospital`. Verify from the host: `psql -h 127.0.0.1 -p 54322 -U hospital_reader -d hospital -c "select patient_id, full_name, phone from patients"` → three rows; and `select 1 from cases` as the same user → `permission denied`.

- [ ] **Step 2: Back up the appointment database**

```bash
docker cp appointment-service-api-1:/data/appointments.db "/c/Users/danie/Documents/ChatGPT/לימודים פרויקט גמר/appointment-service.backup-2026-09-22/appointments.db"
```

Confirm the copy's size is non-zero before going on.

- [ ] **Step 3: Fix the one row in place**

```bash
docker exec appointment-service-api-1 python -c "import sqlite3; c = sqlite3.connect('/data/appointments.db'); n = c.execute(\"UPDATE appointments SET patient_id = 'P-20000' WHERE appointment_id = 'APT-8392' AND patient_id = 'P-10042'\").rowcount; c.commit(); print('rows updated:', n)"
```

Guarded on the old value: `0` rows means the user already changed it, which is fine. Anything other than 0 or 1 → stop.

- [ ] **Step 4: Rebuild and restart the appointment service**

From the appointment-service folder: `docker compose up -d --build`. The volume is kept. Check its log for `patient registry check: enabled`.

- [ ] **Step 5: End to end, from the host**

With `X-API-Key` set to the service's development key from its README (`local-development-api-key`). If that key is refused with `401`, the user's `.env` sets another one: **stop and ask the user for it** - do not read `.env` or the container's environment to find it.

| Request | Expected |
|---|---|
| `P-10041` | `200`, `APT-8391` |
| `P-20000` | `200`, `APT-8392` |
| `P-30000` | `200`, `found: false` |
| `P-99999` | `404 patient_not_found` |
| `P-TIMEOUT` | `504 timeout` |

The `503` path is covered by the unit tests; do not stop the user's database to see it live.

## Done when

- HospitalAgent's suite passes, the golden traces print `35` / `4` / `54`, and `hospital_reader` can read `patients` and nothing else.
- The appointment-service's tests pass, old and new, and a backup of its folder and its database exists.
- The five requests of Task 4 Step 5 answer as the table says, from the running service.
