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
