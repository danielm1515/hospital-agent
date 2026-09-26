"""Sub-project 18 (design D12, D14; docs/spec_corrections.md row 89): whether the Approved
Source Registry approves one `source_id` + `version` right now.

Fix round 1 (I1): this asks the real OPA (`opa_runner.instruction_source_approved`) for
`policy.rego`'s own `instruction_source_approved` rule - the same bundle `PolicyService.decide`
reads (`policy/data/approved_instruction_sources.json`) - rather than re-parsing the registry's
dates in Python. There is one implementation of "is this source approved right now", and OPA is
it; a Python reimplementation could read a timestamp OPA's own `time.parse_rfc3339_ns` would
reject as though it were valid (or vice versa), which is exactly the bug this replaces.
`policy.rego` reads its own clock (`time.now_ns()`) - `now` is never part of the OPA input, so
this function takes none either.
"""
from __future__ import annotations

from .policy import opa_runner


def is_approved(source_id: str, version: str) -> bool | None:
    """Delegates to the real OPA binary: `True` approved, `False` denied, `None` unavailable
    (binary missing, non-zero exit, a timeout, or an unreadable answer - Task 8). Never an
    exception, so a missing or misbehaving policy engine must never turn into a 500 for the
    instruction routes (`api/instructions.py`); only `True` approves."""
    return opa_runner.instruction_source_approved(source_id, version)
