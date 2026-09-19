"""Read tables and golden traces out of docs/spec/ (generated from the binding docx)."""
from __future__ import annotations

import re
from pathlib import Path

SPEC_DIR = Path(__file__).resolve().parents[2] / "docs" / "spec"
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_TRACE_LINE = re.compile(r"^\[(\w+)\s*\]\s+([A-Z_]+)")


def _cells(line: str) -> list[str]:
    return [cell.strip().replace("\\|", "|") for cell in _CELL_SPLIT.split(line.strip())[1:-1]]


def read_table(filename: str, header: tuple[str, ...]) -> list[list[str]]:
    """Data rows of the Markdown table in docs/spec/<filename> whose header equals `header`."""
    lines = (SPEC_DIR / filename).read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("|") and _cells(line) == list(header):
            rows = []
            for row in lines[i + 2 :]:
                if not row.startswith("|"):
                    break
                rows.append(_cells(row))
            return rows
    raise LookupError(f"no table with header {header} in {filename}")


def golden_traces() -> dict[int, list[tuple[str, str]]]:
    """§15: scenario number -> [(state after, event), ...] in trace order."""
    traces: dict[int, list[tuple[str, str]]] = {}
    current: list[tuple[str, str]] | None = None
    for line in (SPEC_DIR / "15-golden-traces.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("SCENARIO "):
            current = traces.setdefault(int(line.split()[1]), [])
        elif (match := _TRACE_LINE.match(line)) and current is not None:
            current.append((match.group(1), match.group(2)))
    return traces
