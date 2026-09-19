r"""A small SLD-resolution Prolog engine that runs rules.pl (spec §10) as written.

Supported subset - exactly what the spec's program and queries use:
  facts and rules; conjunction `,`; negation as failure `\+ Goal` and `\+ (A, B)`;
  cut `!`; atoms, quoted atoms, variables, compound terms, lists `[a, b]`;
  builtins true/0, fail/0, =/2, ==/2, \==/2, atom/1, memberchk/2, assertz/1,
  retract/1. Directives (`:- dynamic ...`, `:- table ...`) are accepted and
  ignored. As in SWI-Prolog for dynamic predicates, a goal with no clauses fails
  instead of raising an existence error.

Each Prolog() instance is an isolated database: the Policy Service builds a
fresh one per request (§10: request-local facts are loaded in isolation).
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import count
from pathlib import Path

RULES_FILE = Path(__file__).with_name("rules.pl")

# --- terms -------------------------------------------------------------------------


@dataclass(frozen=True)
class Var:
    name: str
    uid: int = 0


@dataclass(frozen=True)
class Struct:
    functor: str
    args: tuple = ()


NIL = Struct("[]")
TRUE = Struct("true")


def _cons(head, tail) -> Struct:
    return Struct("[|]", (head, tail))


_PLAIN_ATOM = re.compile(r"[a-z][A-Za-z0-9_]*\Z")


def format_term(term) -> str:
    """Render a resolved term the way the SWI-Prolog toplevel prints it."""
    if isinstance(term, Var):
        return f"_{term.name}"
    if term.functor == "[|]" or term == NIL:
        items = []
        while isinstance(term, Struct) and term.functor == "[|]":
            items.append(format_term(term.args[0]))
            term = term.args[1]
        return "[" + ", ".join(items) + "]"
    name = term.functor if _PLAIN_ATOM.match(term.functor) else "'" + term.functor.replace("'", "\\'") + "'"
    if not term.args:
        return name
    return f"{name}({', '.join(format_term(a) for a in term.args)})"


# --- parser ------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<quoted>'(?:[^'\\]|\\.)*')"
    r"|(?P<op>:-|\?-|\\\+|\\==|==|=)"
    r"|(?P<var>[A-Z_][A-Za-z0-9_]*)"
    r"|(?P<atom>[a-z][A-Za-z0-9_]*)"
    r"|(?P<end>\.(?=\s|$))"
    r"|(?P<punct>[(),\[\]|!])"
    r")"
)
_DIRECTIVE = re.compile(r"^\s*:-.*$", re.MULTILINE)
_COMMENT = re.compile(r"%[^\n]*")


class PrologSyntaxError(ValueError):
    pass


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens, pos = [], 0
    text = text.rstrip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise PrologSyntaxError(f"unexpected input at: {text[pos:pos + 30]!r}")
        pos = match.end()
        kind = match.lastgroup
        value = match.group(kind)
        if kind == "quoted":
            value = value[1:-1].replace("\\'", "'")
        tokens.append((kind, value))
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens, self.pos = tokens, 0
        self.vars: dict[str, Var] = {}
        self.anonymous = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise PrologSyntaxError("unexpected end of input")
        self.pos += 1
        return token

    def expect(self, value: str) -> None:
        kind, got = self.take()
        if got != value:
            raise PrologSyntaxError(f"expected {value!r}, got {got!r}")

    def at(self, value: str) -> bool:
        token = self.peek()
        return token is not None and token[1] == value and token[0] != "quoted"

    def term(self):
        kind, value = self.take()
        if kind == "var":
            if value == "_":
                self.anonymous += 1
                return Var(f"_G{self.anonymous}")
            return self.vars.setdefault(value, Var(value))
        if kind == "punct" and value == "[":
            return self.list_tail()
        if kind == "punct" and value == "!":
            return Struct("!")
        if kind in ("atom", "quoted"):
            if kind == "atom" and self.at("("):
                self.take()
                args = [self.term()]
                while self.at(","):
                    self.take()
                    args.append(self.term())
                self.expect(")")
                return Struct(value, tuple(args))
            return Struct(value)
        raise PrologSyntaxError(f"unexpected token {value!r}")

    def list_tail(self):
        if self.at("]"):
            self.take()
            return NIL
        items = [self.term()]
        while self.at(","):
            self.take()
            items.append(self.term())
        tail = NIL
        if self.at("|"):
            self.take()
            tail = self.term()
        self.expect("]")
        for item in reversed(items):
            tail = _cons(item, tail)
        return tail

    def goal(self):
        if self.at("\\+"):
            self.take()
            return Struct("\\+", (self.goal(),))
        if self.at("("):
            self.take()
            inner = self.body()
            self.expect(")")
            return inner
        left = self.term()
        token = self.peek()
        if token and token[0] == "op" and token[1] in ("=", "==", "\\=="):
            self.take()
            return Struct(token[1], (left, self.term()))
        return left

    def body(self):
        goals = [self.goal()]
        while self.at(","):
            self.take()
            goals.append(self.goal())
        result = goals[-1]
        for goal in reversed(goals[:-1]):
            result = Struct(",", (goal, result))
        return result


@dataclass(frozen=True)
class Clause:
    head: Struct
    body: object  # a goal term; TRUE for facts


def parse_program(text: str) -> list[Clause]:
    text = _DIRECTIVE.sub("", _COMMENT.sub("", text))
    tokens = _tokenize(text)
    clauses, start = [], 0
    for i, (kind, _) in enumerate(tokens):
        if kind == "end":
            parser = _Parser(tokens[start:i])
            head = parser.term()
            body = TRUE
            if parser.at(":-"):
                parser.take()
                body = parser.body()
            if parser.peek() is not None:
                raise PrologSyntaxError(f"trailing tokens in clause for {head.functor}")
            clauses.append(Clause(head, body))
            start = i + 1
    if start != len(tokens):
        raise PrologSyntaxError("clause without a terminating '.'")
    return clauses


def parse_query(text: str) -> tuple[object, dict[str, Var]]:
    text = _COMMENT.sub("", text).strip()
    if text.startswith("?-"):
        text = text[2:]
    tokens = _tokenize(text.strip())
    if tokens and tokens[-1][0] == "end":
        tokens = tokens[:-1]
    parser = _Parser(tokens)
    goal = parser.body()
    if parser.peek() is not None:
        raise PrologSyntaxError("trailing tokens in query")
    return goal, parser.vars


# --- unification -------------------------------------------------------------------


def _walk(term, subst: dict):
    while isinstance(term, Var) and term in subst:
        term = subst[term]
    return term


def _unify(a, b, subst: dict) -> dict | None:
    a, b = _walk(a, subst), _walk(b, subst)
    if a == b:
        return subst
    if isinstance(a, Var):
        return {**subst, a: b}
    if isinstance(b, Var):
        return {**subst, b: a}
    if a.functor != b.functor or len(a.args) != len(b.args):
        return None
    for x, y in zip(a.args, b.args):
        subst = _unify(x, y, subst)
        if subst is None:
            return None
    return subst


def resolve(term, subst: dict):
    term = _walk(term, subst)
    if isinstance(term, Struct) and term.args:
        return Struct(term.functor, tuple(resolve(a, subst) for a in term.args))
    return term


# --- solver ------------------------------------------------------------------------


class _Cut(Exception):
    def __init__(self, barrier: object) -> None:
        self.barrier = barrier


class PrologDepthError(RuntimeError):
    pass


MAX_DEPTH = 400
_fresh = count(1)


def _rename(term, mapping: dict, uid: int):
    if isinstance(term, Var):
        return mapping.setdefault(term, Var(term.name, uid))
    if term.args:
        return Struct(term.functor, tuple(_rename(a, mapping, uid) for a in term.args))
    return term


class Prolog:
    """An isolated clause database with a query interface."""

    def __init__(self, program: str = "") -> None:
        self.clauses: list[Clause] = []
        if program:
            self.consult(program)

    def consult(self, text: str) -> None:
        self.clauses.extend(parse_program(text))

    def assertz(self, fact: str) -> None:
        """Add one ground fact, e.g. assertz("case_step('CASE-1', check_documents)")."""
        [clause] = parse_program(fact.rstrip().rstrip(".") + ".")
        self.clauses.append(clause)

    # -- queries

    def solve(self, query: str, limit: int | None = None) -> list[dict[str, str]]:
        """All answers (up to `limit`) as {variable: rendered term}."""
        goal, variables = parse_query(query)
        answers = []
        for subst in self._run(goal):
            answers.append({name: format_term(resolve(var, subst)) for name, var in variables.items()
                            if not name.startswith("_")})
            if limit is not None and len(answers) >= limit:
                break
        return answers

    def ask(self, query: str) -> bool:
        return bool(self.solve(query, limit=1))

    def _run(self, goal) -> Iterator[dict]:
        barrier = object()
        try:
            yield from self._solve(((goal, barrier),), {}, 0)
        except _Cut as cut:
            if cut.barrier is not barrier:
                raise

    # -- engine

    def _solve(self, goals: tuple, subst: dict, depth: int) -> Iterator[dict]:
        if not goals:
            yield subst
            return
        if depth > MAX_DEPTH:
            raise PrologDepthError("recursion too deep")
        (goal, barrier), rest = goals[0], goals[1:]
        goal = _walk(goal, subst)
        if isinstance(goal, Var):
            raise PrologSyntaxError("unbound goal")
        name, args = goal.functor, goal.args

        if name == "," and len(args) == 2:
            yield from self._solve(((args[0], barrier), (args[1], barrier), *rest), subst, depth)
            return
        if name == "!" and not args:
            yield from self._solve(rest, subst, depth)
            raise _Cut(barrier)
        if name == "true" and not args:
            yield from self._solve(rest, subst, depth)
            return
        if name == "fail" and not args:
            return
        if name == "\\+" and len(args) == 1:
            if not self._has_solution(args[0], subst, depth):
                yield from self._solve(rest, subst, depth)
            return
        if name == "=" and len(args) == 2:
            unified = _unify(args[0], args[1], subst)
            if unified is not None:
                yield from self._solve(rest, unified, depth)
            return
        if name in ("==", "\\==") and len(args) == 2:
            same = resolve(args[0], subst) == resolve(args[1], subst)
            if same == (name == "=="):
                yield from self._solve(rest, subst, depth)
            return
        if name == "atom" and len(args) == 1:
            value = _walk(args[0], subst)
            if isinstance(value, Struct) and not value.args and value != NIL:
                yield from self._solve(rest, subst, depth)
            return
        if name == "memberchk" and len(args) == 2:
            items = _walk(args[1], subst)
            while isinstance(items, Struct) and items.functor == "[|]":
                unified = _unify(args[0], items.args[0], subst)
                if unified is not None:
                    yield from self._solve(rest, unified, depth)
                    return
                items = _walk(items.args[1], subst)
            return
        if name == "assertz" and len(args) == 1:
            self.clauses.append(Clause(resolve(args[0], subst), TRUE))
            yield from self._solve(rest, subst, depth)
            return
        if name == "retract" and len(args) == 1:
            target = resolve(args[0], subst)
            for i, clause in enumerate(self.clauses):
                if clause.body == TRUE:
                    unified = _unify(target, clause.head, subst)
                    if unified is not None:
                        del self.clauses[i]
                        yield from self._solve(rest, unified, depth)
                        return
            return

        inner = object()
        try:
            for clause in [c for c in self.clauses
                           if c.head.functor == name and len(c.head.args) == len(args)]:
                mapping: dict = {}
                uid = next(_fresh)
                head = _rename(clause.head, mapping, uid)
                unified = _unify(goal, head, subst)
                if unified is None:
                    continue
                body = _rename(clause.body, mapping, uid)
                yield from self._solve(((body, inner), *rest), unified, depth + 1)
        except _Cut as cut:
            if cut.barrier is not inner:
                raise

    def _has_solution(self, goal, subst: dict, depth: int) -> bool:
        barrier = object()
        try:
            for _ in self._solve(((goal, barrier),), subst, depth + 1):
                return True
        except _Cut as cut:
            if cut.barrier is not barrier:
                raise
        return False
