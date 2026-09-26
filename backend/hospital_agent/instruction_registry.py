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

import threading
import time
from collections import OrderedDict

from .policy import opa_runner

# Final review M5: a small memo, so every instruction read does not start its own OPA process.
# Only a real answer (True/False) is kept, for CACHE_TTL_SECONDS; an outage (None) is never
# kept, so the next read asks OPA again. Bounded (least recently used goes first) and guarded
# by a lock, since FastAPI runs these sync routes on a thread pool. The memo only serves the
# two read-only instruction routes; the Policy Service's own decision still asks OPA fresh.
CACHE_TTL_SECONDS = 60.0
CACHE_MAX_ENTRIES = 256

_clock = time.monotonic  # replaced in tests
_lock = threading.Lock()
_memo: OrderedDict[tuple[str, str], tuple[float, bool]] = OrderedDict()


def clear_cache() -> None:
    with _lock:
        _memo.clear()


def is_approved(source_id: str, version: str) -> bool | None:
    """Delegates to the real OPA binary: `True` approved, `False` denied, `None` unavailable
    (binary missing, non-zero exit, a timeout, or an unreadable answer - Task 8). Never an
    exception, so a missing or misbehaving policy engine must never turn into a 500 for the
    instruction routes (`api/instructions.py`); only `True` approves. A real answer is reused
    for up to CACHE_TTL_SECONDS (final review M5); `None` never is."""
    key = (source_id, version)
    with _lock:
        hit = _memo.get(key)
        if hit is not None and _clock() - hit[0] < CACHE_TTL_SECONDS:
            _memo.move_to_end(key)
            return hit[1]
    answer = opa_runner.instruction_source_approved(source_id, version)
    if answer is not None:
        with _lock:
            _memo[key] = (_clock(), answer)
            _memo.move_to_end(key)
            while len(_memo) > CACHE_MAX_ENTRIES:
                _memo.popitem(last=False)
    return answer
