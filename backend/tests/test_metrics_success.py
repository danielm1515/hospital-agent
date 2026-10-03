"""Group G - the presentation's success metrics: readiness before the appointment, handling time,
and repeat requests about the same appointment."""
from datetime import UTC, datetime, timedelta

from pytest import approx

from hospital_agent import metrics
from hospital_agent.metrics import Window
from tests.metrics_seed import T0, add_case, add_row, at

WINDOW = Window(T0, T0 + timedelta(days=1))
FUTURE = datetime(2030, 1, 1, 8, 0, tzinfo=UTC)  # after any now() a test runs at


def _resolved(conn, case_id: str, minutes: float) -> None:
    add_row(conn, case_id, "CASE_RESOLVED", at=at(minutes), before="Delivering", after="Completed")


# --- readiness ---------------------------------------------------------------------------


def test_an_appointment_is_ready_when_one_of_its_cases_was_resolved_before_it(app_engine):
    with app_engine.begin() as conn:
        # ready: resolved an hour before the appointment
        add_case(conn, "C-ready", created_at=at(-120), answered_appointment_id="APT-1", appointment_at=at(600))
        _resolved(conn, "C-ready", 540)
        # not ready: resolved only after the appointment
        add_case(conn, "C-late", created_at=at(-120), answered_appointment_id="APT-2", appointment_at=at(300),
                 patient_id="P-20000")
        _resolved(conn, "C-late", 400)
        # not ready: never resolved (escalated)
        add_case(conn, "C-stuck", created_at=at(-120), answered_appointment_id="APT-3", appointment_at=at(700))
        # two cases about one appointment count once: the second one made it ready
        add_case(conn, "C-first", created_at=at(-120), appointment_id="APT-4", appointment_at=at(800))
        add_case(conn, "C-again", created_at=at(-60), appointment_id="APT-4", appointment_at=at(800))
        _resolved(conn, "C-again", 700)
        # outside the window: the appointment was the day before
        add_case(conn, "C-old", created_at=at(-3000), answered_appointment_id="APT-5", appointment_at=at(-60))
    with app_engine.connect() as conn:
        readiness = metrics.success(conn, WINDOW).readiness
    assert (readiness.judged, readiness.ready) == (4, 2)
    assert readiness.rate == approx(0.5)


def test_an_appointment_still_ahead_is_upcoming_not_judged(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-soon", created_at=at(1), answered_appointment_id="APT-9", appointment_at=FUTURE)
        _resolved(conn, "C-soon", 5)
        add_case(conn, "C-soon-2", created_at=at(1), answered_appointment_id="APT-8", appointment_at=FUTURE)
    with app_engine.connect() as conn:
        readiness = metrics.success(conn, WINDOW).readiness
    assert (readiness.judged, readiness.ready, readiness.rate) == (0, 0, None)
    assert (readiness.upcoming, readiness.upcoming_ready) == (2, 1)


def test_a_case_with_no_appointment_is_never_part_of_readiness(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-none", created_at=at(1), appointment_at=at(60))
        _resolved(conn, "C-none", 5)
    with app_engine.connect() as conn:
        readiness = metrics.success(conn, WINDOW).readiness
    assert (readiness.judged, readiness.upcoming) == (0, 0)


# --- handling time -----------------------------------------------------------------------


def test_handling_time_ends_at_the_first_of_resolution_or_hand_off(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-auto", created_at=at(0))
        _resolved(conn, "C-auto", 2)  # 120 s, closed
        add_case(conn, "C-handoff", created_at=at(0))
        add_row(conn, "C-handoff", "HUMAN_REVIEW_REQUIRED", at=at(1), before="Classifying",
                after="AwaitingHumanReview")  # 60 s, handed off; its later resolution does not count
        add_row(conn, "C-handoff", "HUMAN_RESOLVED_CASE", at=at(90), before="AwaitingHumanReview",
                after="Completed")
        add_case(conn, "C-failed", created_at=at(0))
        add_row(conn, "C-failed", "TIMEOUT_EXPIRED", at=at(5), before="AwaitingPatientInput", after="Failed")
        add_case(conn, "C-open", created_at=at(0))  # neither yet
        add_case(conn, "C-blocked", created_at=at(0))
        add_row(conn, "C-blocked", "HUMAN_REVIEW_REQUIRED", at=at(1), record_type="Blocked",
                before="Classifying", after="Classifying")  # a Blocked row hands nothing off
    with app_engine.connect() as conn:
        handling = metrics.success(conn, WINDOW).handling_time
    assert handling.overall.count == 3
    assert handling.overall.max == approx(300)
    assert (handling.closed.count, handling.closed.max) == (2, approx(300))
    assert (handling.handed_off.count, handling.handed_off.p50) == (1, approx(60))
    assert handling.open == 2


def test_handling_time_counts_only_the_cases_opened_in_the_window(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-before", created_at=at(-10))
        _resolved(conn, "C-before", 1)
    with app_engine.connect() as conn:
        handling = metrics.success(conn, WINDOW).handling_time
    assert (handling.overall.count, handling.open) == (0, 0)
    assert handling.overall.p50 is None


# --- repeat requests ---------------------------------------------------------------------


def test_repeat_requests_are_the_cases_beyond_the_first_about_one_appointment(app_engine):
    with app_engine.begin() as conn:
        for n in range(3):  # three about APT-1: two repeats
            add_case(conn, f"C-a{n}", created_at=at(n), answered_appointment_id="APT-1")
        add_case(conn, "C-b", created_at=at(1), appointment_id="APT-2")  # one: no repeat
        # same appointment id, another patient: a different pair
        add_case(conn, "C-c", created_at=at(1), answered_appointment_id="APT-2", patient_id="P-20000")
        # the patient's choice and the service's answer name the same appointment
        add_case(conn, "C-d1", created_at=at(1), appointment_id="APT-3", answered_appointment_id="APT-3")
        add_case(conn, "C-d2", created_at=at(2), answered_appointment_id="APT-3")
        add_case(conn, "C-none", created_at=at(1))  # no appointment at all
        add_case(conn, "C-out", created_at=at(-5), answered_appointment_id="APT-1")  # before the window
    with app_engine.connect() as conn:
        repeats = metrics.success(conn, WINDOW).repeat_requests
    assert repeats.appointments == 4
    assert repeats.repeat_requests == 3
    assert repeats.appointments_with_repeats == 2
    assert repeats.max_requests == 3
    assert repeats.no_appointment == 1


def test_no_cases_means_zero_repeats(app_engine):
    with app_engine.connect() as conn:
        repeats = metrics.success(conn, WINDOW).repeat_requests
    assert (repeats.appointments, repeats.repeat_requests, repeats.max_requests, repeats.no_appointment) == (0, 0, 0, 0)
