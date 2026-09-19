#!/bin/sh
# Runs once, when the Postgres data volume is first created.
# hospital_app is the role the application connects as (INSERT-only on audit_log,
# granted by the Alembic migration). hospital_test is the database pytest uses.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
CREATE ROLE hospital_app LOGIN PASSWORD '${APP_DB_PASSWORD}';
CREATE DATABASE hospital_test OWNER ${POSTGRES_USER};
EOSQL
