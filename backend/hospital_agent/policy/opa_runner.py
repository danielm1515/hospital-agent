"""Evaluate policy.rego (spec §8) with the real OPA binary, as a process in this container.

OPA is the engine that decides at run time (Policy design §3). Every failure mode
fails closed (spec §14):

    binary missing / non-zero exit / timeout / unreadable output -> Deny policy_engine_unavailable
    a result that is not Allow | Deny | RequireHumanReview       -> Deny no_matching_rule
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

POLICY_DIR = Path(__file__).parent
POLICY_FILE = POLICY_DIR / "policy.rego"
DATA_DIR = POLICY_DIR / "data"
QUERY = "data.hospital_agent.policy.decision"
INSTRUCTION_QUERY = "data.hospital_agent.policy.instruction_source_approved"
TIMEOUT_SECONDS = 5
KNOWN_RESULTS = frozenset({"Allow", "Deny", "RequireHumanReview"})


@dataclass(frozen=True)
class OpaDecision:
    result: str
    reasons: tuple[str, ...]


UNAVAILABLE = OpaDecision("Deny", ("policy_engine_unavailable",))


def evaluate(policy_input: Mapping[str, Any], *, opa_binary: str = "opa",
             timeout: float = TIMEOUT_SECONDS) -> OpaDecision:
    command = [opa_binary, "eval", "--format", "json", "--data", str(POLICY_FILE),
               "--data", str(DATA_DIR), "--stdin-input", QUERY]
    try:
        proc = subprocess.run(command, input=json.dumps(policy_input), capture_output=True,
                              text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return UNAVAILABLE
    if proc.returncode != 0:
        return UNAVAILABLE
    try:
        value = json.loads(proc.stdout)["result"][0]["expressions"][0]["value"]
    except (ValueError, KeyError, IndexError, TypeError):
        return UNAVAILABLE
    if not isinstance(value, dict) or value.get("result") not in KNOWN_RESULTS:
        return OpaDecision("Deny", ("no_matching_rule",))
    return OpaDecision(value["result"], tuple(sorted(value.get("reasons", []))))


def instruction_source_approved(source_id: str, version: str, *, opa_binary: str = "opa",
                                 timeout: float = TIMEOUT_SECONDS) -> bool:
    """Sub-project 18 (design D12; docs/spec_corrections.md row 89): the real OPA's own
    `instruction_source_approved` rule (`policy.rego`), evaluated fresh against the same bundle
    `decision` reads - never a second, Python reimplementation of its time parsing (fix round 1
    I1). `policy.rego` reads `time.now_ns()` itself; `now` is not part of the input, exactly
    like `build_opa_input` (`policy/service.py`) never supplies one for the full `decision`
    query either.

    `instruction_source_approved` is a partial rule (no `else` branch), so OPA's own answer is
    *undefined* - not `false` - whenever the body doesn't hold (unlisted source, wrong version,
    empty version, not yet valid, expired, or any date the registry stores in a shape
    `time.parse_rfc3339_ns` can't parse). `opa eval --format json` then omits `"result"`
    entirely, and the same `KeyError`/`IndexError` this function already fails closed on for a
    missing/malformed answer covers that case too - no separate branch needed.

    Fails closed (False) exactly like `evaluate()`: binary missing, non-zero exit, timeout, or
    an answer that isn't literally `true`.
    """
    policy_input = {"instruction_source": {"source_id": source_id, "version": version}}
    command = [opa_binary, "eval", "--format", "json", "--data", str(POLICY_FILE),
               "--data", str(DATA_DIR), "--stdin-input", INSTRUCTION_QUERY]
    try:
        proc = subprocess.run(command, input=json.dumps(policy_input), capture_output=True,
                              text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    try:
        value = json.loads(proc.stdout)["result"][0]["expressions"][0]["value"]
    except (ValueError, KeyError, IndexError, TypeError):
        return False
    return value is True
