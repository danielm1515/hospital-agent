"""How application code builds a State Manager: always with the real Temporal Monitor and
the real ExecutorReverified (Execution design §3.1).

There is no other production path - a State Manager with a permissive monitor or port
exists only in tests. rule_version records the transition table's
version plus a hash of the policy files in force (§12.2).
"""
from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

from sqlalchemy.engine import Engine

from .execution.verify import verify_decision
from .guards import GuardPorts
from .policy.temporal import TemporalMonitor
from .state_manager import RULE_VERSION, StateManager

POLICY_DIR = Path(__file__).with_name("policy")
POLICY_FILES = ("policy.rego", "rules.pl", "flows.dl")


@cache
def policy_version() -> str:
    digest = hashlib.sha256()
    for name in POLICY_FILES:
        digest.update((POLICY_DIR / name).read_bytes())
    return digest.hexdigest()[:12]


def build_state_manager(engine: Engine) -> StateManager:
    """The real Temporal Monitor and the real ExecutorReverified - there is no other port left."""
    return StateManager(engine, TemporalMonitor(), GuardPorts(executor_reverified=verify_decision),
                        rule_version=f"{RULE_VERSION}+policy-{policy_version()}")
