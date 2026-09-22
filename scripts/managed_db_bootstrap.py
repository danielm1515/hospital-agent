"""One-time setup of a managed Postgres (e.g. AWS RDS), which db/init/01-init.sh never reaches.

db/init runs only when the local db container's volume is first created. A managed instance
needs the same two things done once, by its master user, before the backend first migrates
it: the application role (hospital_app) and the two databases (hospital, hospital_test).

Everything comes from the backend's environment - the four database URLs in .env - so this
file holds no secret. Run it inside the backend container, where those are set:

    docker compose run --rm backend python ../scripts/managed_db_bootstrap.py

Idempotent: an existing role keeps its password, and an existing database is left alone.
Nothing here ever drops anything.
"""
from __future__ import annotations

import os
import sys

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def _conninfo(url: str, dbname: str) -> dict:
    u = make_url(url)
    return {
        "host": u.host,
        "port": u.port or 5432,
        "user": u.username,
        "password": u.password,
        "dbname": dbname,
        "sslmode": u.query.get("sslmode", "prefer"),
        "connect_timeout": 10,
    }


def main() -> int:
    owner_url = os.environ["MIGRATION_DATABASE_URL"]
    app = make_url(os.environ["DATABASE_URL"])
    databases = [make_url(owner_url).database, make_url(os.environ["TEST_MIGRATION_DATABASE_URL"]).database]
    if make_url(owner_url).host in (None, "db", "localhost", "127.0.0.1"):
        print("MIGRATION_DATABASE_URL points at the local container - db/init already does this. Nothing to do.")
        return 0

    # The maintenance database: the target databases may not exist yet, and CREATE DATABASE
    # cannot run inside a transaction, hence autocommit.
    with psycopg.connect(**_conninfo(owner_url, "postgres"), autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app.username,)).fetchone():
            print(f"role {app.username}: exists - its password is left unchanged")
        else:
            # CREATE ROLE takes no bind parameter for PASSWORD, so the literal is composed by
            # psycopg (sql.Literal) - never by string formatting (see migration 0004).
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(app.username), sql.Literal(app.password)))
            print(f"role {app.username}: created")

        for name in dict.fromkeys(databases):
            if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                print(f"database {name}: exists - left alone")
            else:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                print(f"database {name}: created")
    return 0


if __name__ == "__main__":
    sys.exit(main())
