"""Test doubles for the ports the Core leaves to later sub-projects.

These live only under tests/ - the application has no permissive defaults.
"""
from __future__ import annotations

from hospital_agent.guards import GuardPorts


def fake_ports(executor_reverified: bool = True) -> GuardPorts:
    return GuardPorts(executor_reverified=lambda ctx: executor_reverified)
