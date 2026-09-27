"""Sub-project 19, Task 3 (design D6): what staff see of the llm_usage rows - a case's cost on
the Case Monitor list (one grouped query per page), a case's usage on its detail, the cost of
a cohort of cases through GET /api/staff/llm-costs, and the same numbers as the admin
metrics' `llm` group inside its one snapshot.

The NULL rule (brief addition B): a case's cost is the SUM of its non-null costs; it is null
only when it has an unpriced row (price_input_per_mtok IS NULL) and no priced cost at all, or no
row at all; a case whose only attempts were priced API errors (NULL tokens, NULL cost) costs 0.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from hospital_agent import llm_costs, metrics
from hospital_agent.api.app import create_app
from hospital_agent.auth import demo_password
from hospital_agent.db import llm_usage
from hospital_agent.metrics import Window
from tests.metrics_seed import T0, add_case, at
from tests.test_api_staff import medical_question

NURSE, ADMIN, PATIENT = "coordinator_nurse", "admin_coordinator", "P-10041"  # clinical_staff, admin_staff
FROM, TO = "2026-09-01T00:00:00+00:00", "2026-09-02T00:00:00+00:00"
WINDOW = Window(T0 - timedelta(hours=8), T0 + timedelta(hours=16))  # 2026-09-01, the whole UTC day
PRICES = (Decimal("0.20"), Decimal("1.20"))


def add_usage(conn, case_id, *, source="agent", call="Intent", model="gpt-5.6-luna",
              tokens=(1000, 0, 100), prices=PRICES, cost="0.00032000", outcome="ok"):
    """One llm_usage row as the recorder would write it. tokens=None is an attempt with no usage
    (an API error); prices=None an unknown model."""
    input_tokens, cached, output = tokens if tokens is not None else (None, None, None)
    price_in, price_out = prices if prices is not None else (None, None)
    conn.execute(llm_usage.insert().values(
        case_id=case_id, source=source, call=call, model=model, outcome=outcome, input_tokens=input_tokens,
        cached_input_tokens=cached, output_tokens=output, price_input_per_mtok=price_in,
        price_output_per_mtok=price_out, cost_usd=None if cost is None else Decimal(cost), created_at=at(5)))


def api_error(conn, case_id, **kwargs):
    add_usage(conn, case_id, tokens=None, cost=None, outcome="api:APITimeoutError", **kwargs)


def unpriced(conn, case_id, **kwargs):
    add_usage(conn, case_id, model="fake", prices=None, cost=None, **kwargs)


# --- the rule, by itself ------------------------------------------------------------------------

@pytest.mark.parametrize("cost_sum, calls, unpriced_calls, expected", [
    (Decimal("0.0003"), 2, 0, Decimal("0.00030000")),  # priced rows
    (Decimal("0.0003"), 3, 1, Decimal("0.00030000")),  # priced cost beside an unpriced row
    (None, 2, 0, Decimal("0")),  # priced API errors only
    (None, 2, 1, None),  # an unpriced row and no priced cost at all
    (None, 0, 0, None),  # no row at all
])
def test_total_cost(cost_sum, calls, unpriced_calls, expected):
    assert llm_costs.total_cost(cost_sum, calls, unpriced_calls) == expected


# --- per case -----------------------------------------------------------------------------------

@pytest.fixture
def seeded(app_engine):
    """C-PRICED two priced rows; C-UNPRICED one unpriced row; C-MIXED a priced row, an unpriced
    row and an API error; C-ERRORS priced API errors only; C-NONE nothing."""
    with app_engine.begin() as conn:
        for case_id in ("C-PRICED", "C-UNPRICED", "C-MIXED", "C-ERRORS", "C-NONE"):
            add_case(conn, case_id, created_at=at(1))
        add_usage(conn, "C-PRICED", call="Intent", tokens=(1000, 200, 100), cost="0.00032000")
        add_usage(conn, "C-PRICED", call="Safety", tokens=(2000, 0, 50), cost="0.00046000")
        unpriced(conn, "C-UNPRICED", source="document_service", call="DocumentClassify")
        add_usage(conn, "C-MIXED", call="Planner", tokens=(500, 0, 10), cost="0.00011200")
        unpriced(conn, "C-MIXED", source="document_service", call="DocumentVision", tokens=(1200, 0, 40))
        api_error(conn, "C-MIXED", call="Planner")
        api_error(conn, "C-ERRORS")
        api_error(conn, "C-ERRORS", call="Safety")
    return app_engine


def test_case_costs_follow_the_null_rule(seeded):
    with seeded.connect() as conn:
        costs = llm_costs.case_costs(conn, ["C-PRICED", "C-UNPRICED", "C-MIXED", "C-ERRORS", "C-NONE"])
    assert costs == {
        "C-PRICED": llm_costs.CaseCost(Decimal("0.00078000"), partial=False, unpriced_calls=0),
        # nothing priced: unknown ("מחיר לא ידוע"), not partial
        "C-UNPRICED": llm_costs.CaseCost(None, partial=False, unpriced_calls=1),
        "C-MIXED": llm_costs.CaseCost(Decimal("0.00011200"), partial=True, unpriced_calls=1),  # a lower bound
        "C-ERRORS": llm_costs.CaseCost(Decimal("0"), partial=False, unpriced_calls=0),
        "C-NONE": llm_costs.CaseCost(None, partial=False, unpriced_calls=0),  # nothing yet ("—")
    }


def test_priced_api_errors_beside_an_unpriced_row_are_unknown_not_partial(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-1", created_at=at(1))
        api_error(conn, "C-1")
        unpriced(conn, "C-1", source="document_service", call="DocumentClassify")
    with app_engine.connect() as conn:
        assert llm_costs.case_costs(conn, ["C-1"]) == {
            "C-1": llm_costs.CaseCost(None, partial=False, unpriced_calls=1)}


def test_case_costs_of_no_case_asks_nothing(app_engine):
    with app_engine.connect() as conn:
        assert llm_costs.case_costs(conn, []) == {}


def test_case_usage_totals_and_breaks_down_by_call(seeded):
    with seeded.connect() as conn:
        usage = llm_costs.case_usage(conn, "C-MIXED")
    assert (usage.calls, usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (3, 1700, 0, 50)
    assert (usage.cost_usd, usage.unpriced_calls) == (Decimal("0.00011200"), 1)
    assert usage.by_call == [
        llm_costs.CallUsage("DocumentVision", 1, 1200, 40, None),
        llm_costs.CallUsage("Planner", 2, 500, 10, Decimal("0.00011200")),
    ]


def test_case_usage_of_a_case_with_no_rows(seeded):
    with seeded.connect() as conn:
        usage = llm_costs.case_usage(conn, "C-NONE")
    assert usage == llm_costs.CaseUsage(0, 0, 0, 0, None, 0, [])


def test_case_usage_of_api_errors_only_costs_zero(seeded):
    with seeded.connect() as conn:
        usage = llm_costs.case_usage(conn, "C-ERRORS")
    assert (usage.calls, usage.input_tokens, usage.cost_usd, usage.unpriced_calls) == (2, 0, Decimal("0"), 0)
    assert [(c.call, c.cost_usd) for c in usage.by_call] == [("Intent", Decimal("0")), ("Safety", Decimal("0"))]


# --- through the staff API ----------------------------------------------------------------------

@pytest.fixture
def client(app_engine):
    with TestClient(create_app(app_engine)) as test_client:
        yield test_client


def headers(client, user_id=NURSE):
    token = client.post("/api/auth/login", json={"user_id": user_id, "password": demo_password()}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def llm_statements(engine):
    """The SQL statements that read llm_usage while the block runs."""
    seen = []

    def spy(conn, cursor, statement, parameters, context, executemany):
        if "llm_usage" in statement:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", spy)
    return seen, lambda: event.remove(engine, "before_cursor_execute", spy)


def test_the_case_list_carries_each_cost_from_one_grouped_query(client, seeded):
    seen, stop = llm_statements(seeded)
    try:
        page = client.get("/api/staff/cases", params={"limit": 10}, headers=headers(client)).json()
    finally:
        stop()
    costs = {item["case_id"]: (item["llm_cost_usd"], item["llm_cost_partial"], item["llm_unpriced_calls"])
             for item in page["items"]}
    assert costs == {"C-PRICED": ("0.00078000", False, 0), "C-UNPRICED": (None, False, 1),
                     "C-MIXED": ("0.00011200", True, 1), "C-ERRORS": ("0.00000000", False, 0),
                     "C-NONE": (None, False, 0)}
    assert len(seen) == 1


def test_a_page_with_no_usage_at_all_is_null_everywhere_still_one_query(client, app_engine):
    with app_engine.begin() as conn:
        for index in range(3):
            add_case(conn, f"C-{index}", created_at=at(index))
    seen, stop = llm_statements(app_engine)
    try:
        page = client.get("/api/staff/cases", headers=headers(client)).json()
    finally:
        stop()
    assert [(item["llm_cost_usd"], item["llm_cost_partial"], item["llm_unpriced_calls"])
            for item in page["items"]] == [(None, False, 0)] * 3
    assert len(seen) == 1


def test_an_empty_page_reads_no_usage(client, app_engine):
    seen, stop = llm_statements(app_engine)
    try:
        assert client.get("/api/staff/cases", headers=headers(client)).json()["items"] == []
    finally:
        stop()
    assert seen == []


def test_each_page_gets_its_own_cases_costs(client, seeded):
    first = client.get("/api/staff/cases", params={"limit": 2}, headers=headers(client)).json()
    second = client.get("/api/staff/cases", params={"limit": 10, "cursor": first["next_cursor"]},
                        headers=headers(client)).json()
    expected = {"C-PRICED": "0.00078000", "C-UNPRICED": None, "C-MIXED": "0.00011200",
                "C-ERRORS": "0.00000000", "C-NONE": None}
    for item in first["items"] + second["items"]:
        assert item["llm_cost_usd"] == expected[item["case_id"]]
    assert len(first["items"]) + len(second["items"]) == 5


def test_the_case_detail_carries_its_usage(client, seeded):
    body = client.get("/api/staff/cases/C-PRICED", headers=headers(client)).json()
    assert body["llm_usage"] == {
        "calls": 2, "input_tokens": 3000, "cached_input_tokens": 200, "output_tokens": 150,
        "cost_usd": "0.00078000", "unpriced_calls": 0,
        "by_call": [
            {"call": "Intent", "calls": 1, "input_tokens": 1000, "output_tokens": 100, "cost_usd": "0.00032000"},
            {"call": "Safety", "calls": 1, "input_tokens": 2000, "output_tokens": 50, "cost_usd": "0.00046000"},
        ],
    }


def test_the_case_detail_of_a_case_with_no_usage(client, seeded):
    body = client.get("/api/staff/cases/C-NONE", headers=headers(client)).json()
    assert body["llm_usage"] == {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                                 "cost_usd": None, "unpriced_calls": 0, "by_call": []}


def test_the_patient_never_reads_a_cost(client, seeded):
    patient = headers(client, PATIENT)
    assert client.get("/api/staff/cases", headers=patient).status_code == 403
    assert client.get("/api/staff/cases/C-PRICED", headers=patient).status_code == 403


def test_usage_rows_never_change_the_shown_context_ref(client, sm, app_engine):
    """ReviewContext's `shown` is hashed into shown_context_ref: usage must stay out of it, or a
    reviewer's decision would be refused as context_changed by mere bookkeeping."""
    d = medical_question(sm, app_engine)
    staff = headers(client)
    before = client.get(f"/api/staff/cases/{d.case_id}/context", headers=staff).json()
    with app_engine.begin() as conn:
        add_usage(conn, d.case_id)
        unpriced(conn, d.case_id, source="document_service", call="DocumentClassify")
    after = client.get(f"/api/staff/cases/{d.case_id}/context", headers=staff).json()
    assert after["shown_context_ref"] == before["shown_context_ref"]
    assert "llm_usage" not in after and "llm_cost_usd" not in after
    assert client.get(f"/api/staff/cases/{d.case_id}", headers=staff).json()["llm_usage"]["calls"] == 2


# --- the cohort: cases opened in the window, all of their usage ---------------------------------

@pytest.fixture
def cohort(app_engine):
    """In the window: C-DONE (Completed, 0.001 + an unpriced row), C-OPEN (0.0003), C-ERR (Completed,
    API errors only), C-FAKE (unpriced only), C-IDLE (nothing). Outside it: C-OLD (0.5)."""
    with app_engine.begin() as conn:
        add_case(conn, "C-DONE", created_at=at(1), state="Completed")
        add_case(conn, "C-OPEN", created_at=at(2), state="AwaitingPatientInput")
        add_case(conn, "C-ERR", created_at=at(3), state="Completed")
        add_case(conn, "C-FAKE", created_at=at(4), state="Completed")
        add_case(conn, "C-IDLE", created_at=at(5))
        add_case(conn, "C-OLD", created_at=T0 - timedelta(days=2), state="Completed")
        add_usage(conn, "C-DONE", call="Intent", tokens=(3000, 1000, 100), cost="0.00072000")
        add_usage(conn, "C-DONE", call="Planner", tokens=(1000, 0, 70), cost="0.00028400")
        unpriced(conn, "C-DONE", source="document_service", call="DocumentVision", tokens=(1200, 0, 40))
        add_usage(conn, "C-OPEN", call="Intent", tokens=(1000, 0, 83), cost="0.00029960")
        api_error(conn, "C-ERR", call="Safety")
        unpriced(conn, "C-FAKE", source="document_service", call="DocumentClassify", tokens=(900, 0, 40))
        add_usage(conn, "C-OLD", call="Intent", tokens=(10, 0, 1), cost="0.50000000")
    return app_engine


def llm_costs_of(client, user_id=NURSE, **params):
    return client.get("/api/staff/llm-costs", params={"from": FROM, "to": TO, **params},
                      headers=headers(client, user_id))


def test_the_cohort_costs(client, cohort):
    response = llm_costs_of(client)
    assert response.status_code == 200
    assert response.json() == {
        "window": {"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"},
        "cases": 5,
        "cases_with_usage": 4,
        "calls": 6,
        "input_tokens": 7100,
        "cached_input_tokens": 1000,
        "output_tokens": 333,
        "total_cost_usd": "0.00130360",
        # C-DONE, C-OPEN and C-ERR have a priced row: 0.0013036 / 3
        "avg_cost_per_case_usd": "0.00043453",
        # Completed with a priced row: C-DONE and C-ERR: 0.001004 / 2
        "avg_cost_per_completed_case_usd": "0.00050200",
        "unpriced_calls": 2,
        "by_call": [
            {"call": "DocumentClassify", "calls": 1, "input_tokens": 900, "output_tokens": 40, "cost_usd": None},
            {"call": "DocumentVision", "calls": 1, "input_tokens": 1200, "output_tokens": 40, "cost_usd": None},
            {"call": "Intent", "calls": 2, "input_tokens": 4000, "output_tokens": 183, "cost_usd": "0.00101960"},
            {"call": "Planner", "calls": 1, "input_tokens": 1000, "output_tokens": 70, "cost_usd": "0.00028400"},
            {"call": "Safety", "calls": 1, "input_tokens": 0, "output_tokens": 0, "cost_usd": "0.00000000"},
        ],
        "by_source": [
            {"source": "agent", "calls": 4, "cost_usd": "0.00130360"},
            {"source": "document_service", "calls": 2, "cost_usd": None},
        ],
    }


def test_a_window_with_cases_but_no_priced_row_has_null_averages(client, app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-FAKE", created_at=at(1), state="Completed")
        unpriced(conn, "C-FAKE", call="Intent")
        add_case(conn, "C-IDLE", created_at=at(2), state="Completed")
    body = llm_costs_of(client).json()
    assert (body["cases"], body["cases_with_usage"], body["calls"], body["unpriced_calls"]) == (2, 1, 1, 1)
    assert (body["total_cost_usd"], body["avg_cost_per_case_usd"], body["avg_cost_per_completed_case_usd"]) == (
        None, None, None)


def test_the_averages_cover_priced_cases_only_so_zero_beside_a_null_total(client, app_engine):
    """docs/api.md §10: one case with only priced API errors (cost 0) and one with only unpriced
    rows (cost unknown). The total is unknown (null), but the averages are over the priced case
    alone, which cost 0."""
    with app_engine.begin() as conn:
        add_case(conn, "C-ERR", created_at=at(1), state="Completed")
        api_error(conn, "C-ERR")
        add_case(conn, "C-FAKE", created_at=at(2), state="Completed")
        unpriced(conn, "C-FAKE", source="document_service", call="DocumentClassify")
    body = llm_costs_of(client).json()
    assert (body["cases"], body["cases_with_usage"], body["calls"], body["unpriced_calls"]) == (2, 2, 2, 1)
    assert body["total_cost_usd"] is None
    assert body["avg_cost_per_case_usd"] == "0.00000000"
    assert body["avg_cost_per_completed_case_usd"] == "0.00000000"


def test_no_completed_priced_case_is_a_null_completed_average_only(client, app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-OPEN", created_at=at(1), state="Planning")
        add_usage(conn, "C-OPEN", cost="0.00000003")
        add_case(conn, "C-OPEN2", created_at=at(2), state="Planning")
        add_usage(conn, "C-OPEN2", cost="0.00000002")
    body = llm_costs_of(client).json()
    assert body["avg_cost_per_case_usd"] == "0.00000003"  # 0.00000005 / 2, half-up
    assert body["avg_cost_per_completed_case_usd"] is None


def test_an_empty_window(client, app_engine):
    body = llm_costs_of(client).json()
    assert {key: body[key] for key in body if key != "window"} == {
        "cases": 0, "cases_with_usage": 0, "calls": 0, "input_tokens": 0, "cached_input_tokens": 0,
        "output_tokens": 0, "total_cost_usd": None, "avg_cost_per_case_usd": None,
        "avg_cost_per_completed_case_usd": None, "unpriced_calls": 0, "by_call": [], "by_source": []}


@pytest.mark.parametrize("user_id", [NURSE, ADMIN])
def test_any_staff_member_reads_the_costs(client, user_id):
    assert llm_costs_of(client, user_id).status_code == 200


def test_the_patient_is_403(client):
    response = llm_costs_of(client, PATIENT)
    assert (response.status_code, response.json()["detail"]) == (403, "staff_only")


def test_no_token_is_401(client):
    response = client.get("/api/staff/llm-costs", params={"from": FROM, "to": TO})
    assert response.status_code == 401


@pytest.mark.parametrize("params, code", [
    ({"from": "yesterday"}, "invalid_range"),
    ({"from": "2026-09-01T00:00:00"}, "invalid_range"),  # no time zone
    ({"from": TO, "to": FROM}, "invalid_range"),
    ({"from": FROM, "to": FROM}, "invalid_range"),
    ({"to": "2026-12-01T00:00:01+00:00"}, "range_too_large"),
])
def test_a_bad_window_is_422_with_its_code(client, params, code):
    response = llm_costs_of(client, **params)
    assert (response.status_code, response.json()["detail"]) == (422, code)


def test_a_missing_parameter_is_the_apps_invalid_body(client):
    response = client.get("/api/staff/llm-costs", params={"from": FROM}, headers=headers(client))
    assert (response.status_code, response.json()["detail"]) == (422, "invalid_body")


def test_a_timeout_is_503_and_never_a_partial_answer(client, monkeypatch):
    def timed_out(engine, window):
        raise metrics.MetricsUnavailable("statement_timeout")

    monkeypatch.setattr(metrics, "compute_llm", timed_out)
    response = llm_costs_of(client)
    assert (response.status_code, response.json()["detail"]) == (503, "llm_costs_unavailable")


def test_the_answer_carries_no_case_or_patient_identifier(client, cohort):
    text_ = llm_costs_of(client).text
    assert "C-DONE" not in text_ and "P-10041" not in text_ and "gpt-5.6-luna" not in text_


# --- the snapshot: the route's and the admin metrics' `llm` group ------------------------------

def test_compute_llm_runs_in_the_read_only_repeatable_read_snapshot(app_engine, monkeypatch):
    seen = {}
    real = metrics.llm

    def spying(conn, window):
        seen["isolation"] = conn.execute(text("SHOW transaction_isolation")).scalar_one()
        seen["read_only"] = conn.execute(text("SHOW transaction_read_only")).scalar_one()
        seen["timeout"] = conn.execute(text("SHOW statement_timeout")).scalar_one()
        return real(conn, window)

    monkeypatch.setattr(metrics, "llm", spying)
    metrics.compute_llm(app_engine, WINDOW)
    assert seen == {"isolation": "repeatable read", "read_only": "on", "timeout": "5s"}
    seen.clear()
    metrics.compute(app_engine, WINDOW, {})
    assert seen == {"isolation": "repeatable read", "read_only": "on", "timeout": "5s"}


def test_a_statement_timeout_refuses_the_llm_costs(app_engine, monkeypatch):
    monkeypatch.setattr(metrics, "STATEMENT_TIMEOUT", "50ms")
    monkeypatch.setattr(metrics, "llm", lambda conn, window: conn.execute(text("SELECT pg_sleep(1)")))
    with pytest.raises(metrics.MetricsUnavailable):
        metrics.compute_llm(app_engine, WINDOW)


def test_the_llm_group_shares_the_other_groups_snapshot(app_engine, monkeypatch):
    """A usage row committed after the snapshot began is invisible to the llm group, like any
    other row is to the other groups."""
    with app_engine.begin() as conn:
        add_case(conn, "C-1", created_at=at(1), state="Completed")
    real_flow = metrics.flow

    def flow_then_write(conn, window):
        result = real_flow(conn, window)
        with app_engine.begin() as other:
            add_usage(other, "C-1")
        return result

    monkeypatch.setattr(metrics, "flow", flow_then_write)
    result = metrics.compute(app_engine, WINDOW, {})
    assert (result.flow.opened, result.llm.cases, result.llm.calls) == (1, 1, 0)
    monkeypatch.setattr(metrics, "flow", real_flow)
    assert metrics.compute(app_engine, WINDOW, {}).llm.calls == 1


def test_the_admin_metrics_carry_the_same_llm_numbers(client, cohort):
    admin = client.get("/api/admin/metrics", params={"from": FROM, "to": TO}, headers=headers(client, ADMIN)).json()
    staff = llm_costs_of(client).json()
    del staff["window"]
    assert admin["llm"] == staff
