"""compute(): every group in one REPEATABLE READ, READ ONLY snapshot (sub-project 14, design §5),
checked on seeded rows and on real flows driven through the State Manager."""
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from hospital_agent import metrics
from hospital_agent.human_review import HumanReviewService
from hospital_agent.metrics import MetricsUnavailable, Window
from hospital_agent.naming import State
from hospital_agent.session import SessionService
from tests.driver import Driver
from tests.metrics_seed import T0, add_case, at
from tests.test_clinical_answer import answer_it, escalated

WINDOW = Window(T0, T0 + timedelta(days=1))
MEDICAL = "Should I stop taking my blood thinner?"


def test_compute_returns_every_group_and_the_sources(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-1", created_at=at(1), state="Completed")
    result = metrics.compute(app_engine, WINDOW, {"appointments": "mock", "documents": None})
    assert result.window == WINDOW
    assert result.flow.opened == 1
    assert result.tools.sources == {"appointments": "mock", "documents": None}
    assert abs((result.generated_at - datetime.now(UTC)).total_seconds()) < 60


def test_the_snapshot_is_repeatable_read_with_a_statement_timeout(app_engine, monkeypatch):
    seen = {}
    real_flow = metrics.flow

    def spying_flow(conn, window):
        seen["isolation"] = conn.execute(text("SHOW transaction_isolation")).scalar_one()
        seen["read_only"] = conn.execute(text("SHOW transaction_read_only")).scalar_one()
        seen["timeout"] = conn.execute(text("SHOW statement_timeout")).scalar_one()
        return real_flow(conn, window)

    monkeypatch.setattr(metrics, "flow", spying_flow)
    metrics.compute(app_engine, WINDOW, {})
    assert seen == {"isolation": "repeatable read", "read_only": "on", "timeout": "5s"}


def test_the_transaction_refuses_a_write(app_engine, monkeypatch):
    monkeypatch.setattr(metrics, "flow", lambda conn, window: conn.execute(text("UPDATE cases SET state = state")))
    with pytest.raises(DBAPIError) as refused:
        metrics.compute(app_engine, WINDOW, {})
    assert isinstance(refused.value.orig, psycopg.errors.ReadOnlySqlTransaction)


def test_a_statement_timeout_refuses_the_whole_answer(app_engine, monkeypatch):
    monkeypatch.setattr(metrics, "STATEMENT_TIMEOUT", "50ms")
    monkeypatch.setattr(metrics, "flow", lambda conn, window: conn.execute(text("SELECT pg_sleep(1)")))
    with pytest.raises(MetricsUnavailable):
        metrics.compute(app_engine, WINDOW, {})


def test_a_clinical_answer_counts_as_a_medical_question_human_resolution(sm, app_engine):
    """Pins the controller ruling (final review): a clinical answer counts under
    MedicalQuestion in human_load.decided_by_kind, because HumanReviewService.answer() passes
    the WorkflowDecision's id - not the ContentApproval's - as the transition's approval_id,
    and decided_by_kind joins on that column (design decision 6)."""
    d = escalated(sm, app_engine)
    review = HumanReviewService(sm, SessionService(sm))

    result = answer_it(review, d)

    assert result.committed and result.state_after is State.COMPLETED
    now = datetime.now(UTC)
    computed = metrics.compute(app_engine, Window(now - timedelta(hours=1), now + timedelta(hours=1)), {})
    assert computed.human_load.decided_by_kind == {"MedicalQuestion": 1}
    assert computed.human_load.decisions["HUMAN_RESOLVED_CASE"] == 1
    assert computed.flow.by_state == {"Completed": 1}


def test_real_flows_show_up_as_the_seeded_shapes_assume(sm, app_engine):
    medical = Driver(sm, app_engine)
    medical.submit()
    medical.validate(MEDICAL)
    medical.medical_question()
    retry = Driver(sm, app_engine, patient_id="P-20000")
    retry.to_classified()
    retry.plan()
    retry.propose()
    retry.allow()
    retry.retry_exhausted()
    now = datetime.now(UTC)
    result = metrics.compute(app_engine, Window(now - timedelta(hours=1), now + timedelta(hours=1)), {})
    assert result.flow.opened == 2
    assert result.flow.by_outcome["MedicalQuestion"] == 1
    assert result.human_load.escalations_entered == 2
    assert result.human_load.open_by_kind == {"MedicalQuestion": 1, "RetryExhausted": 1}
    assert result.human_load.open_now == 2
    assert result.tools.failure_events["RETRY_EXHAUSTED"] == 1
    assert result.policy.decisions["POLICY_ALLOWED"] == 1
