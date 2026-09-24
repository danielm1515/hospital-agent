"""Groups D and E - the patient's SLA and the policy layers (sub-project 14, design §4.4-§4.5)."""
from datetime import timedelta

from pytest import approx

from hospital_agent import metrics
from hospital_agent.metrics import PatientSla, Policy, Window
from tests.metrics_seed import T0, add_case, add_row, at

WINDOW = Window(T0, T0 + timedelta(days=1))
WAITING = "AwaitingPatientInput"
ASK = dict(before="AssessingReadiness", after=WAITING)


def test_each_wait_ends_met_breached_other_or_is_still_waiting(app_engine):
    with app_engine.begin() as conn:
        for case_id in ("C-met", "C-met-late", "C-late", "C-other", "C-wait", "C-twice"):
            add_case(conn, case_id, created_at=at(0))
        add_row(conn, "C-met", "MISSING_INFORMATION_DETECTED", at=at(1), **ASK)
        add_row(conn, "C-met", "DOCUMENT_UPLOADED", at=at(5), before=WAITING, after="Classifying")
        # the window decides which waits count, not when they ended
        add_row(conn, "C-met-late", "MISSING_INFORMATION_DETECTED", at=at(1), **ASK)
        add_row(conn, "C-met-late", "DOCUMENT_UPLOADED", at=T0 + timedelta(days=2), before=WAITING,
                after="Classifying")
        add_row(conn, "C-late", "MISSING_INFORMATION_DETECTED", at=at(1), **ASK)
        add_row(conn, "C-late", "TIMEOUT_EXPIRED", at=at(9), before=WAITING, after="AwaitingHumanReview")
        add_row(conn, "C-other", "MISSING_INFORMATION_DETECTED", at=at(1), **ASK)
        add_row(conn, "C-other", "HUMAN_REVIEW_REQUIRED", at=at(2), before=WAITING, after="AwaitingHumanReview")
        add_row(conn, "C-wait", "MISSING_INFORMATION_DETECTED", at=at(1), **ASK)
        # a rejected upload is a self-loop: the wait goes on
        add_row(conn, "C-wait", "DOCUMENT_UPLOADED", at=at(2), before=WAITING, after=WAITING)
        # two waits on one case: each is ended by the first exit after it
        add_row(conn, "C-twice", "MISSING_INFORMATION_DETECTED", at=at(1), **ASK)
        add_row(conn, "C-twice", "DOCUMENT_UPLOADED", at=at(2), before=WAITING, after="Classifying")
        add_row(conn, "C-twice", "MISSING_INFORMATION_DETECTED", at=at(3), **ASK)
        add_row(conn, "C-twice", "TIMEOUT_EXPIRED", at=at(4), before=WAITING, after="AwaitingHumanReview")
        # a wait that began before the window is not counted
        add_row(conn, "C-met", "MISSING_INFORMATION_DETECTED", at=at(-10), **ASK)
    with app_engine.connect() as conn:
        sla = metrics.patient_sla(conn, WINDOW)
    assert sla == PatientSla(requests=7, met=3, breached=2, other=1, waiting=1, rate=approx(0.6))


def test_no_waits_means_no_rate(app_engine):
    with app_engine.connect() as conn:
        assert metrics.patient_sla(conn, WINDOW) == PatientSla(0, 0, 0, 0, 0, None)


def test_policy_decisions_and_blocked_rows_by_reason_and_event(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-1", created_at=at(0))
        add_row(conn, "C-1", "POLICY_ALLOWED", at=at(1), before="Planning", after="RetrievingData")
        add_row(conn, "C-1", "POLICY_ALLOWED", at=at(2), before="Planning", after="RetrievingData")
        add_row(conn, "C-1", "POLICY_DENIED", at=at(3), before="Planning", after="AwaitingHumanReview")
        # a Blocked row is written with guards = {} and its reason in policy_reasons
        blocked = dict(before="Completed", after="Completed", record_type="Blocked")
        add_row(conn, "C-1", "HUMAN_APPROVED", at=at(4), reasons=["guard_failed"], **blocked)
        add_row(conn, "C-1", "HUMAN_REVIEW_REQUIRED", at=at(5), reasons=["invalid_escalation_reason"], **blocked)
        add_row(conn, "C-1", "HUMAN_APPROVED", at=at(6), reasons=["guard_failed"], **blocked)
        add_row(conn, "C-1", "HUMAN_APPROVED", at=at(-6), reasons=["guard_failed"], **blocked)  # before the window
    with app_engine.connect() as conn:
        result = metrics.policy(conn, WINDOW)
    assert result == Policy(
        decisions={"POLICY_ALLOWED": 2, "POLICY_DENIED": 1, "POLICY_HUMAN_REVIEW_REQUIRED": 0},
        blocked=3,
        blocked_by_reason={"guard_failed": 2, "invalid_escalation_reason": 1},
        blocked_by_event={"HUMAN_APPROVED": 2, "HUMAN_REVIEW_REQUIRED": 1})
