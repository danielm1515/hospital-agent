"""Test doubles for the ports the Core leaves to later sub-projects.

These live only under tests/ - the application has no permissive defaults.
"""
from __future__ import annotations

import threading
from collections.abc import Sequence

from hospital_agent.guards import GuardPorts
from hospital_agent.llm.provider import FakeProvider
from hospital_agent.llm.usage import LLMUsage
from hospital_agent.repository import AuditEntry


def fake_ports(executor_reverified: bool = True) -> GuardPorts:
    return GuardPorts(executor_reverified=lambda ctx: executor_reverified)


class AllowAllMonitor:
    def check(self, trace: Sequence[AuditEntry], candidate: AuditEntry) -> str | None:
        return None


class ViolatingMonitor:
    """Reports `rule` as violated whenever the candidate carries `event`."""

    def __init__(self, event: str, rule: str = "T2") -> None:
        self.event, self.rule = event, rule

    def check(self, trace: Sequence[AuditEntry], candidate: AuditEntry) -> str | None:
        return self.rule if candidate.event == self.event else None


class UnavailableMonitor:
    def check(self, trace: Sequence[AuditEntry], candidate: AuditEntry) -> str | None:
        raise RuntimeError("temporal monitor unavailable")


class BarrierMonitor:
    """Holds every thread applying `event` until all `parties` have read the same case row."""

    def __init__(self, event: str, parties: int) -> None:
        self.event = event
        self.barrier = threading.Barrier(parties, timeout=10)

    def check(self, trace: Sequence[AuditEntry], candidate: AuditEntry) -> str | None:
        if candidate.event == self.event:
            self.barrier.wait()
        return None


class UsageFakeProvider(FakeProvider):
    """FakeProvider that reports `usage` for every answer, the way OpenAIProvider does
    (sub-project 19). Module-level, so the Response Evaluator's spawned process can unpickle it."""

    def __init__(self, usage: LLMUsage, script=None) -> None:
        super().__init__(script)
        self.usage = usage

    def complete_with_usage(self, call, user_input, schema):
        return self.complete(call, user_input, schema), self.usage


class SpyRecorder:
    """Keeps every usage row it is asked to record, in memory (thread-safe)."""

    def __init__(self) -> None:
        self.rows: list[tuple] = []
        self._lock = threading.Lock()

    def record(self, case_id, source, call, model, outcome, usage) -> None:
        with self._lock:
            self.rows.append((case_id, source, call, model, outcome, usage))
