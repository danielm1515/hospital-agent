"""Read the §10 Prolog and §11 Datalog programs and their documented queries out of docs/spec/."""
from __future__ import annotations

import re

from tests.spec_tables import SPEC_DIR

_BLOCK = re.compile(r"```(\w+)\n(.*?)\n```", re.S)
_HEBREW = re.compile(f"[{chr(0x0590)}-{chr(0x05FF)}]")  # the Hebrew block


def code_blocks(filename: str) -> list[tuple[str, str]]:
    return _BLOCK.findall((SPEC_DIR / filename).read_text(encoding="utf-8"))


def _split(block: str) -> tuple[str, list[tuple[str, str]]]:
    """(program text, [(query, documented answer), ...]).

    The program ends at the first `?-` line (or at the Hebrew caption comment just
    above it). A query may span lines until its closing '.'; the next non-comment
    line is its documented answer ("true.", "false." or "R = ...").
    """
    lines = block.splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("?-"))
    if first > 0 and lines[first - 1].startswith("%") and _HEBREW.search(lines[first - 1]):
        first -= 1
    queries: list[tuple[str, str]] = []
    pending: list[str] = []
    for line in lines[first:]:
        if line.startswith("%"):
            continue
        if line.startswith("?-") or pending:
            pending.append(line.strip())
            if line.rstrip().endswith("."):
                queries.append((" ".join(pending), ""))
                pending = []
        else:
            queries[-1] = (queries[-1][0], line.strip())
    return "\n".join(lines[:first]), queries


def prolog_program_and_queries() -> tuple[str, list[tuple[str, str]]]:
    return _split(code_blocks("10-prolog.md")[0][1])


def datalog_program_and_queries() -> tuple[str, list[tuple[str, str]]]:
    return _split(code_blocks("11-datalog.md")[0][1])


def minimized_fields_example() -> str:
    """The JSON block of §11 that build_minimized.py must reproduce."""
    return next(body for lang, body in code_blocks("11-datalog.md") if lang == "json")
