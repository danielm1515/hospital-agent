"""The three scenarios of §0 against the golden traces of §15 - complete, from the running system.

Everything is real except the components sub-projects 4-5 build (hospital_agent.scripted):
State Manager, Policy Service (OPA + Prolog), Readiness Check (Z3), Temporal Monitor and
Tool Executor with the mock external systems. Every (state after, event) line of §15 must
match, including TOOL_EXECUTION_STARTED / AUDIT_RECORDED, and so must the audit row
totals: one row per trace line plus one outcome row per execution.
"""
import subprocess
import sys

import pytest

from hospital_agent.naming import State
from hospital_agent.state_manager import OUTCOME_RECORD_TYPES
from obs.golden import render, run_scenario, trace_lines
from tests.spec_tables import golden_traces


@pytest.mark.parametrize("number, audit_rows", [(1, 35), (2, 4), (3, 54)])
def test_scenario_matches_the_golden_trace(sm, app_engine, number, audit_rows):
    run = run_scenario(number, sm, app_engine)
    assert trace_lines(run.entries) == golden_traces()[number]
    assert len(run.entries) == audit_rows
    assert run.final_state is State.COMPLETED


def test_scenario_3_makes_three_attempts_then_one_after_approval(sm, app_engine):
    run = run_scenario(3, sm, app_engine)
    outcomes = [row.record_type for row in run.entries if row.record_type in OUTCOME_RECORD_TYPES.values()]
    assert outcomes == ["ExecutionSucceeded", "ExecutionFailed", "ExecutionFailed", "ExecutionFailed",
                        "ExecutionSucceeded", "ExecutionSucceeded", "ExecutionSucceeded"]


def test_render_uses_the_spec_15_format(sm, app_engine):
    lines = render(run_scenario(2, sm, app_engine)).splitlines()
    assert lines[0] == "SCENARIO 2  medical escalation"
    assert lines[1].startswith("[Received            ] REQUEST_SUBMITTED")
    assert lines[-1] == "final: Completed   audit rows: 4"


def test_obs_golden_command_prints_the_three_traces(migrated):
    out = subprocess.run([sys.executable, "-m", "obs.golden"], capture_output=True, text=True, check=True).stdout
    finals = [line for line in out.splitlines() if line.startswith("final:")]
    assert finals == ["final: Completed   audit rows: 35", "final: Completed   audit rows: 4",
                      "final: Completed   audit rows: 54"]
