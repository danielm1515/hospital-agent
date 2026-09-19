"""Prolog engine and rules.pl (spec §10)."""
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from hospital_agent.naming import Action, to_prolog
from hospital_agent.policy.prolog import RULES_FILE, Prolog, PrologSyntaxError, Struct, parse_program
from hospital_agent.policy.service import _atom
from tests.spec_programs import prolog_program_and_queries

CASE_482 = Path(__file__).with_name("fixtures") / "case_482.pl"


def spec_engine() -> Prolog:
    engine = Prolog(RULES_FILE.read_text(encoding="utf-8"))
    engine.consult(CASE_482.read_text(encoding="utf-8"))
    return engine


def test_rules_pl_plus_the_case_482_fixture_is_the_spec_program():
    spec_program, _ = prolog_program_and_queries()
    ours = parse_program(RULES_FILE.read_text(encoding="utf-8")) + parse_program(CASE_482.read_text(encoding="utf-8"))
    assert sorted(map(repr, ours)) == sorted(map(repr, parse_program(spec_program)))


@pytest.mark.parametrize("query, answer", prolog_program_and_queries()[1])
def test_spec_10_queries_give_the_documented_answers(query, answer):
    answers = spec_engine().solve(query, limit=1)  # fresh engine: retract/1 must not leak between queries
    if answer in ("true.", "false."):
        assert bool(answers) is (answer == "true.")
    else:
        name, value = answer.removesuffix(".").split(" = ", 1)
        assert answers == [{name: value}]


def test_every_action_in_rules_pl_maps_to_the_action_registry():
    engine = Prolog(RULES_FILE.read_text(encoding="utf-8"))
    names = {a["A"] for a in engine.solve("action_requires_role(A, _)")}
    assert names == {to_prolog(action) for action in Action}


def test_d29_human_actions_are_not_bound_to_the_current_step():
    """D29: InPlan applies to automatic actions only; human actions go through approval facts."""
    engine = spec_engine()
    engine.assertz("workflow_decision_valid('CASE-482', close_medical_case)")
    assert engine.ask("allowed(coordinator_nurse, close_medical_case, 'CASE-482')")
    assert engine.ask("allowed(admin_coordinator, close_medical_case, 'CASE-482')")
    engine.assertz("content_approval_valid('EXEC-482-02', 'P-10041', answer_clinical_q, 'HASH-DEMO-001')")
    assert engine.ask("allowed(coordinator_nurse, answer_clinical_q, 'CASE-482')")


def test_cut_negation_and_conjunction_in_negation():
    engine = Prolog("p(one). p(two). q(X) :- p(X), !. r(X) :- p(X), \\+ (X == two, true).")
    assert engine.solve("q(X)") == [{"X": "one"}]
    assert engine.solve("r(X)") == [{"X": "one"}]


def test_lists_memberchk_atom_and_identity():
    engine = Prolog("flag(true).")
    assert engine.ask("flag(F), memberchk(F, [true, false])")
    assert not engine.ask("memberchk(maybe, [true, false])")
    assert engine.ask("atom(x), x \\== y, x == x")
    assert not engine.ask("atom(X)")
    assert not engine.ask("'' \\== ''")


def test_retract_and_assertz_change_only_this_database():
    first, second = Prolog("f(a)."), Prolog("f(a).")
    assert first.ask("retract(f(a))")
    first.assertz("f(b)")
    assert first.solve("f(X)") == [{"X": "b"}]
    assert second.solve("f(X)") == [{"X": "a"}]


def test_undefined_predicate_fails_like_a_dynamic_one():
    assert not Prolog("p(a).").ask("nothing_here(a)")


def test_anonymous_variables_are_distinct():
    assert Prolog("pair(a, b).").ask("pair(_, _)")


def test_syntax_error_is_reported():
    with pytest.raises(PrologSyntaxError):
        Prolog("p(a")


# M4: engine builtins that may appear as a called goal (control constructs , \+ ! are
# unwrapped by _called_predicates below rather than checked as calls).
_ENGINE_BUILTINS = {
    ("true", 0), ("fail", 0), ("=", 2), ("==", 2), ("\\==", 2),
    ("atom", 1), ("memberchk", 2), ("assertz", 1), ("retract", 1),
}
_DYNAMIC_DIRECTIVE = re.compile(r":-\s*dynamic\s+(.*?)\.", re.DOTALL)
_PREDICATE_INDICATOR = re.compile(r"([a-z][A-Za-z0-9_]*)\s*/\s*(\d+)")


def _declared_dynamic(text: str) -> set[tuple[str, int]]:
    """(Name, Arity) pairs from every `:- dynamic ...` directive - parse_program drops directives."""
    declared = set()
    for directive in _DYNAMIC_DIRECTIVE.findall(text):
        declared |= {(name, int(arity)) for name, arity in _PREDICATE_INDICATOR.findall(directive)}
    return declared


def _called_predicates(goal: object) -> Iterator[tuple[str, int]]:
    """Every (Name, Arity) called in a goal body, control constructs unwrapped rather than reported."""
    if not isinstance(goal, Struct):
        return
    if goal.functor == "," and len(goal.args) == 2:
        yield from _called_predicates(goal.args[0])
        yield from _called_predicates(goal.args[1])
    elif goal.functor == "\\+" and len(goal.args) == 1:
        yield from _called_predicates(goal.args[0])
    elif goal.functor == "!" and not goal.args:
        pass
    else:
        yield (goal.functor, len(goal.args))


def test_every_called_predicate_is_defined_or_dynamic():
    """M4: a typo inside a negated goal (\\+ typo_of_a_real_predicate) would otherwise fail
    open - the goal always fails, so \\+ Goal always succeeds. Guard against that by checking
    every predicate called in rules.pl is defined, declared dynamic, or an engine builtin."""
    text = RULES_FILE.read_text(encoding="utf-8")
    clauses = parse_program(text)
    defined = {(c.head.functor, len(c.head.args)) for c in clauses}
    known = defined | _declared_dynamic(text) | _ENGINE_BUILTINS
    called = {pred for c in clauses for pred in _called_predicates(c.body)}
    assert called <= known, called - known


def test_m3_an_id_with_a_backslash_and_a_quote_round_trips():
    """M3: _atom must escape a backslash before it escapes a quote, or a value containing a
    backslash immediately before a quote produces an unterminated/misparsed quoted atom
    (a\\'b -> 'a\\\\'b' reads as the atom a\\ followed by stray text); the tokenizer must then
    unescape both \\\\ -> \\ and \\' -> ' to recover the exact original value."""
    value = "a" + "\\" + "'" + "b"
    engine = Prolog()
    engine.assertz(f"thing({_atom(value)})")
    [clause] = engine.clauses
    assert clause.head.args[0].functor == value
    assert engine.ask(f"thing({_atom(value)})")
