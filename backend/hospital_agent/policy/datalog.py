r"""Bottom-up Datalog evaluator with stratified negation - runs flows.dl (spec §11) as written.

Ported from the AI_Hospital project. Negation (`\+`) is allowed only on a
predicate in a strictly lower stratum, so `blocked_external_flow` has one well
defined answer. `:- table` directives are accepted and ignored: bottom-up
evaluation already terminates on recursive rules such as reaches/2.

Datalog defines, OPA enforces: build_minimized.py exports minimized/2 to the OPA
data file, and the `field_not_minimized` rule in policy.rego is the runtime block.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROGRAM_FILE = Path(__file__).with_name("flows.dl")

_LITERAL = re.compile(r"(?P<neg>\\\+)?\s*(?P<pred>[a-z_][A-Za-z0-9_]*)\((?P<args>[^)]*)\)")
_COMMENT = re.compile(r"%[^\n]*")
_DIRECTIVE = re.compile(r"^\s*:-.*$", re.MULTILINE)


@dataclass(frozen=True)
class Literal:
    pred: str
    args: tuple[str, ...]
    negated: bool = False

    @property
    def ground(self) -> bool:
        return not any(_is_var(a) for a in self.args)


@dataclass(frozen=True)
class Rule:
    head: Literal
    body: tuple[Literal, ...]


class DatalogSyntaxError(ValueError):
    pass


class NotStratifiable(ValueError):
    """Negation forms a cycle, so the program has no well defined answer."""


def _is_var(token: str) -> bool:
    return bool(token) and (token[0].isupper() or token[0] == "_")


def _parse_literal(text: str) -> Literal:
    match = _LITERAL.fullmatch(text.strip())
    if not match:
        raise DatalogSyntaxError(f"cannot parse literal {text!r}")
    args = tuple(a.strip() for a in match.group("args").split(",") if a.strip())
    return Literal(match.group("pred"), args, negated=bool(match.group("neg")))


def _split_body(text: str) -> list[str]:
    """Split on commas that are not inside parentheses."""
    parts, depth, current = [], 0, ""
    for ch in text:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return parts


def parse(text: str) -> tuple[set[Literal], list[Rule]]:
    facts: set[Literal] = set()
    rules: list[Rule] = []
    text = _DIRECTIVE.sub("", _COMMENT.sub("", text))
    for statement in (s.strip() for s in text.split(".") if s.strip()):
        if ":-" in statement:
            head, body = statement.split(":-", 1)
            rules.append(Rule(_parse_literal(head), tuple(_parse_literal(b) for b in _split_body(body))))
        else:
            fact = _parse_literal(statement)
            if not fact.ground:
                raise DatalogSyntaxError(f"fact {statement!r} is not ground")
            facts.add(fact)
    return facts, rules


def _stratify(rules: list[Rule], predicates: set[str]) -> dict[str, int]:
    stratum = {p: 0 for p in predicates}
    for _ in range(len(predicates) + 1):
        changed = False
        for rule in rules:
            for lit in rule.body:
                need = stratum[lit.pred] + (1 if lit.negated else 0)
                if need > stratum[rule.head.pred]:
                    stratum[rule.head.pred] = need
                    changed = True
        if not changed:
            return stratum
    raise NotStratifiable("negation cycle in the Datalog program")


class Datalog:
    def __init__(self, program: str | None = None) -> None:
        text = program if program is not None else PROGRAM_FILE.read_text(encoding="utf-8")
        self.facts, self.rules = parse(text)
        predicates = ({f.pred for f in self.facts} | {r.head.pred for r in self.rules}
                      | {lit.pred for r in self.rules for lit in r.body})
        self.stratum = _stratify(self.rules, predicates)
        self.model = self._evaluate()

    def _match(self, lit: Literal, binding: dict[str, str], model: set[Literal]) -> list[dict[str, str]]:
        out = []
        for fact in model:
            if fact.pred != lit.pred or len(fact.args) != len(lit.args):
                continue
            trial = dict(binding)
            for pattern, value in zip(lit.args, fact.args):
                if _is_var(pattern):
                    if trial.setdefault(pattern, value) != value:
                        break
                elif pattern != value:
                    break
            else:
                out.append(trial)
        return out

    def _fire(self, rule: Rule, model: set[Literal]) -> set[Literal]:
        bindings: list[dict[str, str]] = [{}]
        for lit in rule.body:
            if lit.negated:
                kept = []
                for b in bindings:
                    grounded = Literal(lit.pred, tuple(b.get(a, a) for a in lit.args))
                    if not grounded.ground:
                        raise DatalogSyntaxError(f"unsafe negation in a rule for {rule.head.pred}")
                    if grounded not in model:
                        kept.append(b)
                bindings = kept
            else:
                bindings = [b2 for b in bindings for b2 in self._match(lit, b, model)]
            if not bindings:
                return set()
        return {Literal(rule.head.pred, tuple(b.get(a, a) for a in rule.head.args)) for b in bindings}

    def _evaluate(self) -> set[Literal]:
        model = set(self.facts)
        for level in sorted(set(self.stratum.values())):
            level_rules = [r for r in self.rules if self.stratum[r.head.pred] == level]
            while True:
                new: set[Literal] = set()
                for rule in level_rules:
                    new |= self._fire(rule, model)
                if new <= model:
                    break
                model |= new
        return model

    def ask(self, query: str) -> bool:
        """Ground query such as "?- blocked_external_flow(patient_text, appointment_system).\""""
        text = query.strip().removeprefix("?-").strip().removesuffix(".")
        lit = _parse_literal(text)
        if not lit.ground:
            raise DatalogSyntaxError("ask() takes a ground query")
        return lit in self.model

    def relation(self, pred: str) -> list[tuple[str, ...]]:
        return sorted(f.args for f in self.model if f.pred == pred)
