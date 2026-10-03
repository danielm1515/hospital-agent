"""Reset the demo: empty every system's runtime data and keep its reference data.

    python scripts/reset_demo.py            # shows the counts, asks for RESET, resets
    python scripts/reset_demo.py --dry-run  # shows what would be emptied, changes nothing
    python scripts/reset_demo.py --yes      # no question

What each system keeps:
- hospital-agent (Postgres): patients (the registry) and users (the five staff and their
  passwords). Everything a case wrote - cases, audit_log, data_log, approvals, executions,
  llm_usage, upload_attempts - is emptied and its ids restart at 1.
- appointment-service (SQLite): admin_users (with the Authenticator secret), exam_types,
  document_types. Appointments, their joins and their audit log are emptied. No demo seed:
  book the appointments in the admin screen after a reset.
- document-service (SQLite): documents and document_audit_logs are emptied. S3 is not touched -
  the objects it held simply become unreferenced.

Each system is reached with `docker compose exec` in its own folder, so no password is read or
printed here. The hospital-agent backend is stopped for the reset (its orchestrator polls the
cases every few seconds) and started again afterwards, whatever happened.

This empties an append-only audit trail on purpose: it is a local demo reset, never something
to point at a shared or production database.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent

# hospital-agent: every table in backend/hospital_agent/db.py is in exactly one of these
# (tests/test_reset_demo.py holds that, so a new table must be classified on purpose).
AGENT_RESET = ("cases", "executions", "audit_log", "approvals", "data_log", "llm_usage", "upload_attempts")
AGENT_KEEP = ("patients", "users")

# Children before parents, so a delete never trips a foreign key.
APPOINTMENT_RESET = ("appointment_required_documents", "appointment_exam_types", "appointments",
                     "appointment_audit_logs")
DOCUMENT_RESET = ("documents", "document_audit_logs")


@dataclass(frozen=True)
class SqliteSystem:
    name: str
    folder: Path
    db_path: str
    tables: tuple[str, ...]


SQLITE_SYSTEMS = (
    SqliteSystem("appointment-service",
                 Path(os.environ.get("APPOINTMENT_SERVICE_DIR", PARENT / "appointment-service")),
                 "/data/appointments.db", APPOINTMENT_RESET),
    SqliteSystem("document-service",
                 Path(os.environ.get("DOCUMENT_SERVICE_DIR", PARENT / "document-service")),
                 "/data/documents.db", DOCUMENT_RESET),
)


def sqlite_count_code(db_path: str, tables: list[str] | tuple[str, ...]) -> str:
    """Python run inside a service container: prints {table: rows} as one JSON line."""
    return (
        "import json, sqlite3\n"
        f"conn = sqlite3.connect({db_path!r})\n"
        f"COUNTS = {{t: conn.execute('SELECT count(*) FROM \"' + t + '\"').fetchone()[0] for t in {list(tables)!r}}}\n"
        "conn.close()\n"
        "print(json.dumps(COUNTS))\n"
    )


def sqlite_reset_code(db_path: str, tables: list[str] | tuple[str, ...]) -> str:
    """Python run inside a service container: empties the tables in one transaction and restarts
    their AUTOINCREMENT ids. All or nothing - an error rolls the whole system back."""
    return (
        "import sqlite3\n"
        f"conn = sqlite3.connect({db_path!r}, isolation_level=None)\n"
        "conn.execute('BEGIN IMMEDIATE')\n"
        "try:\n"
        f"    for t in {list(tables)!r}:\n"
        "        conn.execute('DELETE FROM \"' + t + '\"')\n"
        "    if conn.execute(\"SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'\").fetchone():\n"
        f"        conn.executemany('DELETE FROM sqlite_sequence WHERE name = ?', [(t,) for t in {list(tables)!r}])\n"
        "    conn.execute('COMMIT')\n"
        "except BaseException:\n"
        "    conn.execute('ROLLBACK')\n"
        "    raise\n"
        "finally:\n"
        "    conn.close()\n"
    )


def _run(args: list[str], cwd: Path, stdin: str | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, input=stdin, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:4])}… in {cwd.name} failed:\n{result.stderr.strip()}")
    return result.stdout


def _psql(sql: str) -> str:
    # The variables are the db container's own (POSTGRES_USER / POSTGRES_DB), read inside it.
    return _run(["docker", "compose", "exec", "-T", "db", "sh", "-c",
                 'psql -v ON_ERROR_STOP=1 -qtA -U "$POSTGRES_USER" -d "$POSTGRES_DB"'], ROOT, stdin=sql)


def _running(folder: Path, service: str) -> bool:
    out = _run(["docker", "compose", "ps", "--status", "running", "--services"], folder)
    return service in out.split()


def agent_counts() -> dict[str, int]:
    tables = AGENT_RESET + AGENT_KEEP
    sql = " UNION ALL ".join(f"SELECT '{t}', count(*) FROM {t}" for t in tables) + ";"
    return {name: int(n) for name, n in (line.split("|") for line in _psql(sql).split())}


def sqlite_counts(system: SqliteSystem) -> dict[str, int]:
    out = _run(["docker", "compose", "exec", "-T", "api", "python", "-"], system.folder,
               stdin=sqlite_count_code(system.db_path, system.tables))
    return json.loads(out.strip().splitlines()[-1])


def all_counts() -> dict[str, dict[str, int]]:
    counts = {"hospital-agent": agent_counts()}
    for system in SQLITE_SYSTEMS:
        counts[system.name] = sqlite_counts(system)
    return counts


def show(counts: dict[str, dict[str, int]], title: str) -> None:
    print(f"\n{title}")
    for system, tables in counts.items():
        print(f"  {system}")
        for table, n in tables.items():
            kept = "  (נשמר)" if table in AGENT_KEEP else ""
            print(f"    {table:<32}{n:>6}{kept}")


def reset() -> None:
    _run(["docker", "compose", "stop", "backend"], ROOT)
    try:
        # One statement, one transaction; no CASCADE, so a table outside the list that still
        # points at cases makes it fail loudly instead of being emptied along the way.
        _psql(f"TRUNCATE {', '.join(AGENT_RESET)} RESTART IDENTITY;")
        print("  hospital-agent       אופס")
        for system in SQLITE_SYSTEMS:
            _run(["docker", "compose", "exec", "-T", "api", "python", "-"], system.folder,
                 stdin=sqlite_reset_code(system.db_path, system.tables))
            print(f"  {system.name:<20} אופס")
    finally:
        _run(["docker", "compose", "start", "backend"], ROOT)
        print("  backend הופעל מחדש")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Hebrew on a Windows console
    parser = argparse.ArgumentParser(description="איפוס הדמו: מנקה את נתוני הריצה בכל המערכות.")
    parser.add_argument("--dry-run", action="store_true", help="מציג מה יימחק, בלי למחוק")
    parser.add_argument("--yes", action="store_true", help="בלי שאלת אישור")
    args = parser.parse_args()

    missing = [f"{folder.name}:{service}" for folder, service in
               [(ROOT, "db"), (ROOT, "backend")] + [(s.folder, "api") for s in SQLITE_SYSTEMS]
               if not _running(folder, service)]
    if missing:
        print(f"לא רץ: {', '.join(missing)} - הפעל עם docker compose up -d ונסה שוב. לא נמחק כלום.")
        return 1

    show(all_counts(), "לפני האיפוס:")
    if args.dry_run:
        print("\n--dry-run: לא נמחק כלום.")
        return 0
    if not args.yes and input("\nלאיפוס הקלד RESET: ").strip() != "RESET":
        print("בוטל. לא נמחק כלום.")
        return 1

    print()
    reset()
    show(all_counts(), "אחרי האיפוס:")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
