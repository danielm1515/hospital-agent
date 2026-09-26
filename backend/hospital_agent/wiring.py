"""How application code builds a State Manager: always with the real Temporal Monitor and
the real ExecutorReverified (Execution design §3.1).

There is no other production path - a State Manager with a permissive monitor or port
exists only in tests. rule_version records the transition table's
version plus a hash of the policy files in force (§12.2), and - when the server runs the
LLM components - the model and the hash of its four prompts (§18.5).
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
# Fix round 1 (I2): the OPA bundle's own data files - the approved-sources registry and the
# minimized-fields export - are part of the policy in force exactly as much as the three engine
# files, so a registry change (e.g. approving a new instruction source, Task 4) must also change
# rule_version - otherwise two audit rows could carry the same rule_version while OPA actually
# decided under two different registries.
POLICY_FILES = ("policy.rego", "rules.pl", "flows.dl", "data/approved_instruction_sources.json",
                "data/minimized_fields.json")


@cache
def policy_version() -> str:
    digest = hashlib.sha256()
    for name in POLICY_FILES:
        digest.update((POLICY_DIR / name).read_bytes())
    return digest.hexdigest()[:12]


def build_state_manager(engine: Engine, llm_version: str | None = None) -> StateManager:
    """The real Temporal Monitor and the real ExecutorReverified - there is no other port left.

    llm_version: model_selector.llm_version(provider) when the LLM components run.
    """
    rule_version = f"{RULE_VERSION}+policy-{policy_version()}"
    if llm_version is not None:
        rule_version = f"{rule_version}+{llm_version}"
    return StateManager(engine, TemporalMonitor(), GuardPorts(executor_reverified=verify_decision),
                        rule_version=rule_version)
