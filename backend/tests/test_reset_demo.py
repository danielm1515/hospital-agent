"""scripts/reset_demo.py: which tables a demo reset empties, and the SQLite reset it runs."""
import importlib.util
import sqlite3
import sys
from pathlib import Path

from hospital_agent import db

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "reset_demo.py"
_spec = importlib.util.spec_from_file_location("reset_demo", _SCRIPT)
reset_demo = importlib.util.module_from_spec(_spec)
sys.modules["reset_demo"] = reset_demo  # dataclasses look their module up there
_spec.loader.exec_module(reset_demo)


def test_every_table_is_either_reset_or_kept():
    """A table added later must be classified on purpose, never left out of a reset by accident."""
    tables = set(db.metadata.tables)
    reset, keep = set(reset_demo.AGENT_RESET), set(reset_demo.AGENT_KEEP)
    assert reset.isdisjoint(keep)
    assert reset | keep == tables


def test_the_reset_keeps_the_patients_and_the_users():
    assert {"patients", "users"} <= set(reset_demo.AGENT_KEEP)


def _sqlite_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE admin_users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
            CREATE TABLE appointments (appointment_id TEXT PRIMARY KEY);
            CREATE TABLE appointment_required_documents (
                appointment_id TEXT REFERENCES appointments(appointment_id) ON DELETE CASCADE, code TEXT);
            CREATE TABLE appointment_audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT);
            INSERT INTO admin_users (name) VALUES ('admin');
            INSERT INTO appointments VALUES ('APT-1'), ('APT-2');
            INSERT INTO appointment_required_documents VALUES ('APT-1', 'CBC');
            INSERT INTO appointment_audit_logs (event) VALUES ('created'), ('created');
            """
        )


def test_the_sqlite_reset_empties_only_the_listed_tables_and_restarts_their_ids(tmp_path):
    path = tmp_path / "appointments.db"
    _sqlite_db(path)
    tables = ["appointment_required_documents", "appointments", "appointment_audit_logs"]

    exec(reset_demo.sqlite_reset_code(str(path), tables), {})

    with sqlite3.connect(path) as conn:
        assert [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables] == [0, 0, 0]
        assert conn.execute("SELECT count(*) FROM admin_users").fetchone()[0] == 1
        conn.execute("INSERT INTO appointment_audit_logs (event) VALUES ('after')")
        assert conn.execute("SELECT max(id) FROM appointment_audit_logs").fetchone()[0] == 1


def test_the_sqlite_count_reports_each_table(tmp_path):
    path = tmp_path / "appointments.db"
    _sqlite_db(path)
    scope: dict = {}
    exec(reset_demo.sqlite_count_code(str(path), ["appointments", "admin_users"]), scope)
    assert scope["COUNTS"] == {"appointments": 2, "admin_users": 1}
