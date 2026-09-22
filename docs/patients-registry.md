# Patients registry - the contract for other systems

The one table in this project another system may read. `docs/api.md` is the contract for the
UI; this file is the contract for everything else.

## Connecting

**Where the registry lives now: AWS RDS.** The project's `.env` points the live database at an
RDS instance, so an external reader connects there, from the host or from a container alike:

| | Value |
|---|---|
| Host | `hospital.cf42em6cy852.eu-north-1.rds.amazonaws.com` |
| Port | `5432` |
| Database | `hospital` |
| User | `hospital_reader` |
| Password | the `READER_DB_PASSWORD` in HospitalAgent's `.env` - a generated one, never the dev default |
| TLS | required: `sslmode=require` (RDS offers TLS; `verify-full` additionally needs the RDS CA bundle) |

```
postgresql+psycopg://hospital_reader:<READER_DB_PASSWORD>@hospital.cf42em6cy852.eu-north-1.rds.amazonaws.com:5432/hospital?sslmode=require
```

**One thing is weaker on RDS than locally:** `hospital_reader` can create large objects there.
Migration 0004 revokes `EXECUTE` on `lo_creat` / `lo_create` / `lo_from_bytea` from `PUBLIC`, but
on RDS those functions belong to `rdsadmin`, so the master user's `REVOKE` only warns. Everything
else below still holds on RDS - verified as `hospital_reader` against the instance. The instance
is reachable from the internet, so restrict its security group to the addresses that need it.

The local container, for when `.env` does not override the URLs:

| | From the host | From another Docker container |
|---|---|---|
| Host | `127.0.0.1` | `host.docker.internal` |
| Port | `54322` | `54322` |
| Database | `hospital` | `hospital` |
| User | `hospital_reader` | `hospital_reader` |
| Password | `READER_DB_PASSWORD` (default `hospital_reader_dev`) | same |

Use `127.0.0.1`, not `localhost`: on this machine `localhost` resolves to `::1` first, and the
port is published on IPv4 only.

`host.docker.internal` works on Docker Desktop (Windows, macOS), which forwards it to the host's
loopback, where the port is published. On Linux it does not work as things stand:
`extra_hosts: ["host.docker.internal:host-gateway"]` resolves the name to the Docker bridge
address, and Postgres is published on `127.0.0.1:54322` only, so the connection is refused and
the appointment-service answers every lookup with `503`. Linux would need the port published on
an address the bridge can reach (e.g. the bridge gateway, `172.17.0.1:54322`), or the two
compose projects joined on a shared Docker network. HospitalAgent's own binding stays
`127.0.0.1`: changing it is the owner's call, not this contract's.

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

Migration 0004 grants and confines it, in each database it migrates (`hospital` and
`hospital_test`):

- It can `SELECT` on `patients`. That is its only table privilege.
- It cannot read `cases`, `audit_log`, `data_log`, `approvals` or `executions` - that is where
  the agent keeps the medical content and the audit trace (§12.3).
- It cannot insert, update, delete or truncate `patients`, nor create a table in `public`.
- It cannot create large objects (`lo_creat`, `lo_create` and `lo_from_bytea` are revoked from
  `PUBLIC`; server-side `lo_import` / `lo_export` are superuser-only in Postgres 16) or
  temporary tables (`TEMPORARY` on the database is revoked from `PUBLIC`).
- It holds at most 5 connections at once (`CONNECTION LIMIT 5`). A client of this role should
  keep its pool at 4 or fewer, leaving one for a manual `psql` session (the appointment-service
  uses `pool_size=2, max_overflow=2`).
- It is a plain login role: 0004 refuses to migrate if an existing `hospital_reader` is a
  superuser, has `CREATEROLE`, `CREATEDB`, `REPLICATION` or `BYPASSRLS`, or is a member of any
  other role. An existing role keeps its password.

What it still can do, stated plainly because a per-database migration does not own it:

- It can connect to the cluster's other databases (`postgres`, `template1`), where `CONNECT`
  and `TEMPORARY` are still granted to `PUBLIC` and large objects can still be created -
  revoking those is cluster-wide configuration, outside what this repo's migration touches.
- It can read catalog and statistics metadata, as every role can: for example the names and
  columns of tables it cannot read, and their row-count estimates (`pg_class.reltuples`,
  `pg_stat_user_tables`). It cannot read their rows.

The local container listens on `127.0.0.1` only, so there all of this is reachable from this
machine alone; the RDS instance is reachable from wherever its security group allows. The
appointment-service reads the RDS copy: its own `.env` sets `PATIENT_REGISTRY_URL` to the URL
above. After a downgrade of 0004 the table and the revokes are gone, but the role stays and can still
log in (it is cluster-wide and may hold grants in the other database).

Read only the columns you need. A check that a patient exists needs
`SELECT 1 FROM patients WHERE patient_id = $1`, not the name and phone. Never write a name or a
phone into an application log.

The appointment-service reads two things: that existence check, on every lookup and every
booking, and `SELECT patient_id, full_name FROM patients ORDER BY patient_id` for its logged-in
admin screen - the booking form's patient dropdown, and the name shown next to each
appointment. It never reads `phone`. The dropdown is a convenience only: a booking asks the
existence check again, so an id sent around the list is still refused.

## Adding a patient

The patients also exist in the demo IdP, `DEMO_USERS` in `backend/hospital_agent/auth.py`, and
`backend/tests/test_patients.py` fails if the two disagree. So a new patient means a new
migration that inserts the row, **and** the same patient in `DEMO_USERS`.
