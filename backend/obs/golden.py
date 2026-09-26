"""python -m obs.golden - the §15 golden traces, produced by the running system.

Spec §15: "ה־traces להלן נגזרו ידנית ... לפני הגשה יש להחליפם בפלט המערכת: python3 -m obs.golden".
Each scenario runs end to end on the real Agent Orchestrator and LLM components (with the
deterministic FakeProvider), State Manager, Policy Service (OPA + Prolog), Readiness Check
(Z3), Temporal Monitor and Tool Executor against the mock external systems; only the
Session Service and the reviewers, which sub-project 5 builds, are scripted
(hospital_agent.scripted). Only patient input and the mock's script change between
scenarios (§0).

It runs on the test database (Execution design decision 6): it migrates it and clears
its tables first, and never touches the main database.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from hospital_agent.execution.gateway import MockGateway
from hospital_agent.llm.model_selector import llm_version
from hospital_agent.llm.orchestrator import Orchestrator
from hospital_agent.llm.provider import FakeProvider
from hospital_agent.naming import Event, State
from hospital_agent.policy.temporal import trace_rows
from hospital_agent.repository import AuditEntry
from hospital_agent.scripted import ScriptedAgents
from hospital_agent.state_manager import StateManager
from hospital_agent.wiring import build_state_manager

BACKEND_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ScenarioRun:
    number: int
    title: str
    final_state: State
    entries: list[AuditEntry]  # every audit row of the case, in order


MEDICAL_REQUEST = "Should I stop taking my blood thinner before the colonoscopy?"


def _scenario_1(patient: ScriptedAgents, agent: Orchestrator) -> None:
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)   # classify, plan, three retrievals, Z3: ask for blood_test
    patient.upload("blood_test")
    agent.run_case(patient.case_id)   # re-classified, readiness passes, the status message is sent


def _scenario_2(patient: ScriptedAgents, agent: Orchestrator) -> None:
    patient.submit()
    patient.validate(MEDICAL_REQUEST)
    agent.run_case(patient.case_id)   # MedicalQuestion: a human decides
    patient.human(Event.HUMAN_RESOLVED_CASE, patient.approval("resolve"))


def _scenario_3(patient: ScriptedAgents, agent: Orchestrator) -> None:
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)   # the document system times out three times: RetryExhausted
    patient.human(Event.HUMAN_APPROVED, patient.approval("approve"))  # fixed - a new, bounded retry cycle
    agent.run_case(patient.case_id)
    patient.upload("blood_test")
    agent.run_case(patient.case_id)


Scenario = Callable[[ScriptedAgents, Orchestrator], None]

# number -> (title, the mock's script, the scenario)
SCENARIOS: dict[int, tuple[str, Callable[[], MockGateway], Scenario]] = {
    1: ("normal operational flow", MockGateway, _scenario_1),
    2: ("medical escalation", MockGateway, _scenario_2),
    3: ("technical failure  (3 automatic attempts)", lambda: MockGateway(failures={"CheckDocuments": 3}), _scenario_3),
}


def run_scenario(number: int, sm: StateManager, engine: Engine) -> ScenarioRun:
    """The real Agent Orchestrator with the deterministic FakeProvider (LLM design §8)."""
    title, gateway, play = SCENARIOS[number]
    patient = ScriptedAgents(sm, engine)
    agent = Orchestrator(sm, FakeProvider(), gateway())
    try:
        play(patient, agent)
    finally:
        agent.close()
    return ScenarioRun(number, title, patient.state, patient.trace())


def trace_lines(entries: list[AuditEntry]) -> list[tuple[str, str]]:
    """(state after, event) of each trace row - what §15 lists; outcome and Blocked rows are not lines."""
    return [(row.state_after, row.event) for row in trace_rows(entries)]


def _notes(row: AuditEntry) -> str:
    """Display only - the tests compare states, events and counts."""
    if row.event in (Event.TOOL_TRANSIENT_FAILURE, Event.RETRY_EXHAUSTED):
        return f"attempt={row.attempt_number}"
    if row.event == Event.ACTION_PROPOSED:
        return f"action={row.action}"
    if row.event in (Event.HUMAN_APPROVED, Event.HUMAN_REJECTED, Event.HUMAN_RESOLVED_CASE):
        return f"approval={row.approval_id}"
    if row.event == Event.TOOL_EXECUTION_STARTED:
        return f"execution={row.execution_id} attempt={row.attempt_number}"
    return ""


def render(run: ScenarioRun) -> str:
    lines = [f"SCENARIO {run.number}  {run.title}"]
    for row in trace_rows(run.entries):
        lines.append(f"[{row.state_after:<20}] {row.event:<28} {_notes(row)}".rstrip())
    lines.append(f"final: {run.final_state.value}   audit rows: {len(run.entries)}")
    return "\n".join(lines)


def _prepare_test_database() -> Engine:
    owner_url = os.environ["TEST_MIGRATION_DATABASE_URL"]
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["url"] = owner_url
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    owner = create_engine(owner_url)
    with owner.begin() as conn:
        conn.execute(text("TRUNCATE llm_usage, data_log, audit_log, approvals, executions, cases RESTART IDENTITY"))
    owner.dispose()
    return create_engine(os.environ["TEST_DATABASE_URL"])


def main() -> int:
    engine = _prepare_test_database()
    try:
        sm = build_state_manager(engine, llm_version(FakeProvider()))
        print("\n\n".join(render(run_scenario(number, sm, engine)) for number in SCENARIOS))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
