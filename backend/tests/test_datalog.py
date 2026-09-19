"""Datalog engine and flows.dl (spec §11)."""
import json

import pytest

from hospital_agent.policy import build_minimized
from hospital_agent.policy.datalog import PROGRAM_FILE, Datalog, DatalogSyntaxError, NotStratifiable, parse
from tests.spec_programs import datalog_program_and_queries, minimized_fields_example


def test_flows_dl_is_the_spec_program():
    spec_program, _ = datalog_program_and_queries()
    assert parse(PROGRAM_FILE.read_text(encoding="utf-8")) == parse(spec_program)


@pytest.mark.parametrize("query, answer", datalog_program_and_queries()[1])
def test_spec_11_queries_give_the_documented_answers(query, answer):
    assert answer in ("true.", "false.")
    assert Datalog().ask(query) is (answer == "true.")


def test_recursion_and_negation():
    d = Datalog("edge(a, b). edge(b, c). node(a). node(b). node(c). "
                "path(X, Y) :- edge(X, Y). path(X, Z) :- edge(X, Y), path(Y, Z). "
                "unreachable(X) :- node(X), \\+ path(a, X).")
    assert d.relation("path") == [("a", "b"), ("a", "c"), ("b", "c")]
    assert d.relation("unreachable") == [("a",)]


def test_negation_cycle_is_rejected():
    with pytest.raises(NotStratifiable):
        Datalog("p(X) :- q(X), \\+ r(X). r(X) :- q(X), \\+ p(X). q(a).")


def test_non_ground_fact_is_rejected():
    with pytest.raises(DatalogSyntaxError):
        Datalog("sensitive(X).")


def test_export_matches_the_spec_11_json():
    assert build_minimized.export() == json.loads(minimized_fields_example())


def test_committed_opa_data_is_a_fresh_export():
    """Datalog defines, OPA enforces: the data file OPA reads must come from flows.dl."""
    assert build_minimized.OUTPUT.read_text(encoding="utf-8") == build_minimized.render(build_minimized.export())
