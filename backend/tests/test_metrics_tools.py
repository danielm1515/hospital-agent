"""Group C - the external tools (sub-project 14, design §4.3)."""
from datetime import timedelta

from pytest import approx

from hospital_agent import metrics
from hospital_agent.metrics import Durations, FailureReason, ToolAction, Window
from tests.metrics_seed import T0, add_case, add_execution, add_row, at

WINDOW = Window(T0, T0 + timedelta(days=1))
SECOND = timedelta(seconds=1)


def test_calls_success_rate_and_latency_per_action(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-1", created_at=at(0))
        add_execution(conn, "C-1", "CheckAppointment", "succeeded", started_at=at(1), finished_at=at(1) + 2 * SECOND)
        add_execution(conn, "C-1", "CheckAppointment", "succeeded", started_at=at(2), finished_at=at(2) + 4 * SECOND)
        add_execution(conn, "C-1", "CheckAppointment", "failed", started_at=at(3), finished_at=at(3) + 6 * SECOND,
                      attempt=2)
        # still running: counted as a call, not measured, not in the success rate
        add_execution(conn, "C-1", "CheckAppointment", "started", started_at=at(4))
        add_execution(conn, "C-1", "CheckDocuments", "unknown", started_at=at(5), finished_at=at(5) + SECOND)
        # never started (the executions row's first status): in no window
        add_execution(conn, "C-1", "CheckDocuments", "intent", started_at=None)
        add_execution(conn, "C-1", "CheckDocuments", "succeeded", started_at=at(-1), finished_at=at(-1) + SECOND)
    with app_engine.connect() as conn:
        tools = metrics.tools(conn, WINDOW)
    # [2, 4, 6]: p50 = 4, p95 = 4 + 0.9 * 2 = 5.8
    assert tools.actions == [
        ToolAction("CheckAppointment", {"failed": 1, "started": 1, "succeeded": 2}, approx(2 / 3),
                   Durations(3, approx(4.0), approx(5.8), approx(6.0))),
        ToolAction("CheckDocuments", {"unknown": 1}, 0.0, Durations(1, approx(1.0), approx(1.0), approx(1.0))),
    ]
    assert tools.retried_calls == 1


def test_failures_by_event_and_by_reason(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-1", created_at=at(0))
        add_row(conn, "C-1", "TOOL_TRANSIENT_FAILURE", at=at(1), before="RetrievingData", after="Planning")
        add_row(conn, "C-1", "TOOL_TRANSIENT_FAILURE", at=at(2), before="RetrievingData", after="Planning")
        add_row(conn, "C-1", "RETRY_EXHAUSTED", at=at(3), before="RetrievingData", after="AwaitingHumanReview")
        # the outcome rows, as the State Manager writes them: the reason in policy_reasons
        failed = dict(before="RetrievingData", after="RetrievingData", record_type="ExecutionFailed")
        add_row(conn, "C-1", "TOOL_TRANSIENT_FAILURE", at=at(1), reasons=["tool:transient_failure:timeout"], **failed)
        add_row(conn, "C-1", "TOOL_TRANSIENT_FAILURE", at=at(2), reasons=["tool:transient_failure:unavailable"],
                **failed)
        add_row(conn, "C-1", "RETRY_EXHAUSTED", at=at(3), reasons=["tool:transient_failure:timeout"], **failed)
        add_row(conn, "C-1", "HUMAN_REVIEW_REQUIRED", at=at(4), before="RetrievingData", after="RetrievingData",
                record_type="ExecutionUnknown")
        add_row(conn, "C-1", "DATA_RETRIEVED", at=at(5), before="RetrievingData", after="RetrievingData",
                record_type="ExecutionSucceeded")
    with app_engine.connect() as conn:
        tools = metrics.tools(conn, WINDOW)
    assert tools.failure_events == {"TOOL_TRANSIENT_FAILURE": 2, "RETRY_EXHAUSTED": 1}
    assert tools.failure_reasons == [
        FailureReason("ExecutionFailed", "tool:transient_failure:timeout", 2),
        FailureReason("ExecutionFailed", "tool:transient_failure:unavailable", 1),
        FailureReason("ExecutionUnknown", None, 1),
    ]


def test_an_empty_window_has_no_calls(app_engine):
    with app_engine.connect() as conn:
        tools = metrics.tools(conn, WINDOW)
    assert tools == metrics.Tools(actions=[], failure_events={"TOOL_TRANSIENT_FAILURE": 0, "RETRY_EXHAUSTED": 0},
                                  failure_reasons=[], retried_calls=0, sources={})
