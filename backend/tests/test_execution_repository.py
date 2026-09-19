"""Migration 0002's columns and the repository functions the execution layer uses."""
from datetime import UTC, datetime, timedelta

from hospital_agent import repository
from hospital_agent.case import CaseRecord, ExecutionRecord
from hospital_agent.naming import State

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _case(case_id: str, state: State = State.AWAITING_PATIENT_INPUT, **fields) -> CaseRecord:
    return CaseRecord(case_id=case_id, patient_id="P-10041", state=state, state_version=1,
                      created_at=NOW, updated_at=NOW, **fields)


def _execution(execution_id: str, status: str = "intent") -> ExecutionRecord:
    return ExecutionRecord(execution_id=execution_id, case_id="CASE-1", patient_id="P-10041", action="CheckDocuments",
                           step=2, retry_cycle=0, attempt_number=1, idempotency_key=f"CASE-1:2:0:{execution_id}",
                           status=status, decision_token="tok", state_version=5, plan_hash="h" * 64,
                           approval_id="APPR-1", content_hash="HASH-1", medical_content_flag=True)


def test_new_columns_round_trip(app_engine):
    with app_engine.begin() as conn:
        repository.insert_case(conn, _case("CASE-1", appointment_at=NOW + timedelta(hours=96)))
        repository.insert_execution(conn, _execution("EXEC-1"))
    with app_engine.connect() as conn:
        assert repository.load_case(conn, "CASE-1").appointment_at == NOW + timedelta(hours=96)
        assert repository.load_execution(conn, "EXEC-1") == _execution("EXEC-1")


def test_set_execution_status_moves_only_from_the_expected_status(app_engine):
    with app_engine.begin() as conn:
        repository.insert_case(conn, _case("CASE-1"))
        repository.insert_execution(conn, _execution("EXEC-1"))
        assert repository.set_execution_status(conn, "EXEC-1", ("intent",), "started", NOW) == 1
        assert repository.set_execution_status(conn, "EXEC-1", ("intent",), "started", NOW) == 0
        assert repository.set_execution_status(conn, "EXEC-1", ("started",), "succeeded", NOW) == 1
    with app_engine.connect() as conn:
        row = repository.load_execution(conn, "EXEC-1")
        assert (row.status, row.started_at, row.finished_at) == ("succeeded", NOW, NOW)
        assert repository.executions_with_status(conn, "succeeded") == [row]
        assert repository.executions_with_status(conn, "started") == []


def test_expired_patient_deadlines(app_engine):
    with app_engine.begin() as conn:
        repository.insert_case(conn, _case("CASE-PAST", patient_deadline=NOW - timedelta(minutes=1)))
        repository.insert_case(conn, _case("CASE-FUTURE", patient_deadline=NOW + timedelta(minutes=1)))
        repository.insert_case(conn, _case("CASE-OTHER", State.PLANNING, patient_deadline=NOW - timedelta(hours=1)))
    with app_engine.connect() as conn:
        assert [case.case_id for case in repository.expired_patient_deadlines(conn, NOW)] == ["CASE-PAST"]
