"""Group A - the flow of the cases opened in the window (sub-project 14, design §4.1)."""
from datetime import timedelta

import pytest
from pytest import approx

from hospital_agent import metrics
from hospital_agent.metrics import EMPTY_DURATIONS, Durations, InvalidWindow, Window
from tests.metrics_seed import T0, add_case, add_row, at

WINDOW = Window(T0, T0 + timedelta(days=1))


def test_a_window_is_aware_ordered_and_at_most_90_days():
    for start, end in ((T0.replace(tzinfo=None), T0 + timedelta(hours=1)),
                       (T0, T0.replace(tzinfo=None) + timedelta(hours=1)),
                       (T0 + timedelta(hours=1), T0),
                       (T0, T0)):
        with pytest.raises(InvalidWindow) as invalid:
            Window(start, end)
        assert invalid.value.code == "invalid_range"
    assert Window(T0, T0 + timedelta(days=90)).params == {"start": T0, "end": T0 + timedelta(days=90)}
    with pytest.raises(InvalidWindow) as too_long:
        Window(T0, T0 + timedelta(days=90, seconds=1))
    assert too_long.value.code == "range_too_large"


def test_opened_cases_are_counted_by_state_inside_the_window_only(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-start", created_at=T0, state="Completed")  # exactly at start: in
        add_case(conn, "C-mid", created_at=at(60), state="AwaitingHumanReview")
        add_case(conn, "C-end", created_at=T0 + timedelta(days=1), state="Completed")  # exactly at end: out
        add_case(conn, "C-before", created_at=at(-1), state="Failed")
    with app_engine.connect() as conn:
        flow = metrics.flow(conn, WINDOW)
    assert flow.opened == 2
    assert flow.by_state == {"Completed": 1, "AwaitingHumanReview": 1}


def test_the_classification_outcome_reads_the_audit_not_intent_alone(app_engine):
    medical = dict(before="Classifying", after="AwaitingHumanReview")
    with app_engine.begin() as conn:
        # A real medical question leaves cases.intent NULL (RECORD_CLASSIFICATION runs only on
        # INTENT_CLASSIFIED) - the audit row is the only trace of it.
        add_case(conn, "C-med", created_at=at(1))
        add_row(conn, "C-med", "MEDICAL_QUESTION_DETECTED", at=at(2), **medical)
        add_case(conn, "C-safety", created_at=at(1))
        add_row(conn, "C-safety", "HUMAN_REVIEW_REQUIRED", at=at(2), **medical)
        add_case(conn, "C-prep", created_at=at(1), intent="AppointmentPreparation")
        add_case(conn, "C-unsup", created_at=at(1), intent="Unsupported")
        add_case(conn, "C-new", created_at=at(1))
        # a Blocked row is not a classification
        add_row(conn, "C-new", "MEDICAL_QUESTION_DETECTED", at=at(2), before="Received", after="Received",
                record_type="Blocked", reasons=["guard_failed"])
        # a later medical question outranks the intent the case was first classified with
        add_case(conn, "C-both", created_at=at(1), intent="AppointmentPreparation")
        add_row(conn, "C-both", "MEDICAL_QUESTION_DETECTED", at=at(3), **medical)
    with app_engine.connect() as conn:
        flow = metrics.flow(conn, WINDOW)
    assert flow.by_outcome == {"MedicalQuestion": 2, "EscalatedAtClassification": 1,
                               "AppointmentPreparation": 1, "Unsupported": 1, "NotClassified": 1}


def test_time_to_completion_is_split_by_how_the_case_completed(app_engine):
    done = dict(before="Delivering", after="Completed")
    with app_engine.begin() as conn:
        for case_id in ("C-auto1", "C-auto2", "C-human"):
            add_case(conn, case_id, created_at=at(0), state="Completed")
        add_row(conn, "C-auto1", "CASE_RESOLVED", at=at(1), **done)
        add_row(conn, "C-auto2", "CASE_RESOLVED", at=at(3), **done)
        add_row(conn, "C-human", "HUMAN_RESOLVED_CASE", at=at(10), before="AwaitingHumanReview", after="Completed")
    with app_engine.connect() as conn:
        flow = metrics.flow(conn, WINDOW)
    # [60, 180]: p50 = 120, p95 = 60 + 0.95 * 120 = 174
    assert flow.completion["CASE_RESOLVED"] == Durations(2, approx(120.0), approx(174.0), approx(180.0))
    assert flow.completion["HUMAN_RESOLVED_CASE"] == Durations(1, approx(600.0), approx(600.0), approx(600.0))


def test_an_empty_window_has_zero_counts_and_no_percentiles(app_engine):
    with app_engine.connect() as conn:
        flow = metrics.flow(conn, WINDOW)
    assert (flow.opened, flow.by_state, flow.by_outcome) == (0, {}, {})
    assert flow.completion == {"CASE_RESOLVED": EMPTY_DURATIONS, "HUMAN_RESOLVED_CASE": EMPTY_DURATIONS}
