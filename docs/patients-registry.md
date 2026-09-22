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
