"""The §12.3 Data Log (migration 0003): content kept apart from Audit, deleted by tombstone."""
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from hospital_agent import data_log, repository
from hospital_agent.case import new_case
from hospital_agent.data_log import DataKind

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def case_id(app_engine) -> str:
    with app_engine.begin() as conn:
        repository.insert_case(conn, new_case("CASE-1", "P-10041", NOW))
    return "CASE-1"


def test_record_keeps_the_content_and_its_sha256(app_engine, case_id):
    with app_engine.begin() as conn:
        entry = data_log.record(conn, case_id, "P-10041", DataKind.REQUEST_TEXT, "When is my appointment?", NOW)
    assert entry.content_hash == hashlib.sha256("When is my appointment?".encode()).hexdigest()
    with app_engine.connect() as conn:
        assert data_log.entries(conn, case_id, DataKind.REQUEST_TEXT) == [entry]
        assert data_log.entries(conn, case_id, DataKind.UPLOADED_DOCUMENT) == []


def test_entries_are_oldest_first(app_engine, case_id):
    with app_engine.begin() as conn:
        first = data_log.record(conn, case_id, "P-10041", DataKind.UPLOADED_DOCUMENT, "one", NOW)
        second = data_log.record(conn, case_id, "P-10041", DataKind.UPLOADED_DOCUMENT, "two", NOW + timedelta(seconds=1))
    with app_engine.connect() as conn:
        assert [e.entry_id for e in data_log.entries(conn, case_id, DataKind.UPLOADED_DOCUMENT)] == \
            [first.entry_id, second.entry_id]


def test_tombstone_clears_the_content_and_keeps_the_hash(app_engine, case_id):
    with app_engine.begin() as conn:
        entry = data_log.record(conn, case_id, "P-10041", DataKind.OUTGOING_MESSAGE, "message", NOW)
        assert data_log.tombstone(conn, entry.entry_id, NOW) == 1
        assert data_log.tombstone(conn, entry.entry_id, NOW) == 0  # already a tombstone
    with app_engine.connect() as conn:
        [stored] = data_log.entries(conn, case_id, DataKind.OUTGOING_MESSAGE)
    assert (stored.content, stored.content_hash, stored.deleted_at) == (None, entry.content_hash, NOW)


def test_the_app_role_cannot_delete_data_log_rows(app_engine, case_id):
    with app_engine.begin() as conn:
        data_log.record(conn, case_id, "P-10041", DataKind.INSTRUCTIONS, "instructions", NOW)
    with pytest.raises(ProgrammingError, match="permission denied"), app_engine.begin() as conn:
        conn.execute(text("DELETE FROM data_log"))


def test_kind_is_limited_to_the_four_kinds(app_engine, case_id):
    with pytest.raises(Exception, match="ck_data_log_kind"), app_engine.begin() as conn:
        conn.execute(text("INSERT INTO data_log VALUES ('D1', 'CASE-1', 'P-10041', 'other', 'x', 'h', now(), NULL)"))
