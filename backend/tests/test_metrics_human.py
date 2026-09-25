"""Group B - the load on people (sub-project 14, design §4.2)."""
from datetime import UTC, datetime, timedelta

from pytest import approx

from hospital_agent import metrics
from hospital_agent.metrics import EMPTY_DURATIONS, Durations, Window
from tests.metrics_seed import T0, add_approval, add_case, add_row, at

WINDOW = Window(T0, T0 + timedelta(days=1))
REVIEW = "AwaitingHumanReview"


def test_entries_decisions_kinds_and_time_to_decision(app_engine):
    with app_engine.begin() as conn:
        # C-1: escalated at 0, rejected at 30 min
        add_case(conn, "C-1", created_at=at(-5), state="Failed")
        add_row(conn, "C-1", "MEDICAL_QUESTION_DETECTED", at=at(0), before="Classifying", after=REVIEW)
        add_approval(conn, "APPR-1", "C-1", "MedicalQuestion", granted_at=at(30), decision="reject")
        add_row(conn, "C-1", "HUMAN_REJECTED", at=at(30), before=REVIEW, after="Failed", approval_id="APPR-1")
        # C-2: escalated twice - resumed after 10 min, escalated again, resolved after 20 min
        add_case(conn, "C-2", created_at=at(-5), state="Completed")
        add_row(conn, "C-2", "RETRY_EXHAUSTED", at=at(0), before="RetrievingData", after=REVIEW)
        add_approval(conn, "APPR-2a", "C-2", "RetryExhausted", granted_at=at(10), decision="approve")
        add_row(conn, "C-2", "HUMAN_APPROVED", at=at(10), before=REVIEW, after="Planning", approval_id="APPR-2a")
        add_row(conn, "C-2", "RETRY_EXHAUSTED", at=at(40), before="RetrievingData", after=REVIEW)
        add_approval(conn, "APPR-2b", "C-2", "RetryExhausted", granted_at=at(60))
        add_row(conn, "C-2", "HUMAN_RESOLVED_CASE", at=at(60), before=REVIEW, after="Completed", approval_id="APPR-2b")
        # design decision 6: _grant writes its approval in its own transaction, so a blocked
        # decision leaves one behind - it must not count as a decision.
        add_approval(conn, "APPR-orphan", "C-2", "RetryExhausted", granted_at=at(61), decision="approve")
        add_row(conn, "C-2", "HUMAN_APPROVED", at=at(61), before="Completed", after="Completed",
                record_type="Blocked", reasons=["guard_failed"])
    with app_engine.connect() as conn:
        load = metrics.human_load(conn, WINDOW)
    assert load.escalations_entered == 3
    assert load.decisions == {"HUMAN_APPROVED": 1, "HUMAN_RESOLVED_CASE": 1, "HUMAN_REJECTED": 1,
                               "PATIENT_REPLY_REQUESTED": 0}
    assert load.decided_by_kind == {"MedicalQuestion": 1, "RetryExhausted": 2}
    # [600, 1200, 1800]: p50 = 1200, p95 = 1200 + 0.9 * 600 = 1740
    assert load.time_to_decision == Durations(3, approx(1200.0), approx(1740.0), approx(1800.0))


def test_the_open_queue_is_now_not_the_window(app_engine):
    now = datetime.now(UTC)
    with app_engine.begin() as conn:
        add_case(conn, "C-open", created_at=now - timedelta(hours=6), state=REVIEW, escalation_kind="PolicyDenied")
        # an earlier stint, already decided: the case's wait is measured from its latest entry
        add_row(conn, "C-open", "POLICY_DENIED", at=now - timedelta(hours=5), before="Planning", after=REVIEW)
        add_row(conn, "C-open", "HUMAN_APPROVED", at=now - timedelta(hours=4), before=REVIEW, after="Planning")
        add_row(conn, "C-open", "POLICY_DENIED", at=now - timedelta(hours=2), before="Planning", after=REVIEW)
        add_case(conn, "C-open2", created_at=now - timedelta(hours=1), state=REVIEW, escalation_kind="PolicyDenied")
        add_row(conn, "C-open2", "POLICY_DENIED", at=now - timedelta(minutes=30), before="Planning", after=REVIEW)
    with app_engine.connect() as conn:
        load = metrics.human_load(conn, WINDOW)  # WINDOW is 2026-09-01: nothing happened in it
    assert load.escalations_entered == 0
    assert load.time_to_decision == EMPTY_DURATIONS
    assert load.open_now == 2
    assert load.open_by_kind == {"PolicyDenied": 2}
    assert 7200 <= load.oldest_open_seconds < 7200 + 120


def test_a_request_to_the_patient_is_counted_with_the_staff_decisions(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-1", created_at=at(-5), state="AwaitingPatientReply")
        add_row(conn, "C-1", "MEDICAL_QUESTION_DETECTED", at=at(0), before="Classifying", after=REVIEW)
        add_row(conn, "C-1", "PATIENT_REPLY_REQUESTED", at=at(15), before=REVIEW, after="AwaitingPatientReply")
    with app_engine.connect() as conn:
        load = metrics.human_load(conn, WINDOW)
    assert load.decisions["PATIENT_REPLY_REQUESTED"] == 1
    assert load.time_to_decision.count == 1  # the first human action ends the wait (design §13)


def test_an_empty_database_has_no_load(app_engine):
    with app_engine.connect() as conn:
        load = metrics.human_load(conn, WINDOW)
    assert load == metrics.HumanLoad(
        escalations_entered=0,
        decisions={"HUMAN_APPROVED": 0, "HUMAN_RESOLVED_CASE": 0, "HUMAN_REJECTED": 0,
                   "PATIENT_REPLY_REQUESTED": 0},
        decided_by_kind={}, open_by_kind={}, time_to_decision=EMPTY_DURATIONS,
        open_now=0, oldest_open_seconds=None)
