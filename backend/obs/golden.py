"""python -m obs.golden - the §15 golden traces, produced by the running system.

Spec §15: "ה־traces להלן נגזרו ידנית ... לפני הגשה יש להחליפם בפלט המערכת: python3 -m obs.golden".
Each scenario runs end to end on the real State Manager, Policy Service (OPA + Prolog),
Readiness Check (Z3), Temporal Monitor and Tool Executor against the mock external
systems; only the components sub-projects 4-5 will build are scripted
(hospital_agent.scripted). Only the mock's script changes between scenarios (§0).

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


def _scenario_1(a: ScriptedAgents) -> None:
    a.submit()
    a.validate()
    a.classify()
    a.plan()
    a.run_step()          # CheckAppointment
    a.advance()
    a.run_step()          # CheckDocuments: blood_test is missing
    a.advance()
    a.run_step()          # LoadInstructions -> AssessingReadiness
    a.assess()            # Z3 unsat: safe to ask the patient
    a.upload("blood_test")
    a.classify()          # re-classified, readiness in progress
    a.assess()            # everything held -> Ready
    a.plan_delivery()
    a.run_step()          # SendStatusUpdate -> Completed


def _scenario_2(a: ScriptedAgents) -> None:
    a.submit()
    a.validate()
    a.medical_question()
    a.human(Event.HUMAN_RESOLVED_CASE, a.approval("resolve"))


def _scenario_3(a: ScriptedAgents) -> None:
    a.submit()
    a.validate()
    a.classify()
    a.plan()
    a.run_step()          # CheckAppointment
    a.advance()
    for _ in range(3):    # the document system times out three times; no fourth attempt
        a.run_step()
    a.human(Event.HUMAN_APPROVED, a.approval("approve"))  # fixed - a new, bounded retry cycle
    a.run_step()          # CheckDocuments succeeds
    a.advance()
    a.run_step()          # LoadInstructions
    a.assess()
    a.upload("blood_test")
    a.classify()
    a.assess()
    a.plan_delivery()
    a.run_step()          # SendStatusUpdate


# number -> (title, the mock's script, the scenario)
SCENARIOS: dict[int, tuple[str, Callable[[], MockGateway], Callable[[ScriptedAgents], None]]] = {
    1: ("normal operational flow", MockGateway, _scenario_1),
    2: ("medical escalation", MockGateway, _scenario_2),
    3: ("technical failure  (3 automatic attempts)", lambda: MockGateway(failures={"CheckDocuments": 3}), _scenario_3),
}


def run_scenario(number: int, sm: StateManager, engine: Engine) -> ScenarioRun:
    title, gateway, play = SCENARIOS[number]
    agents = ScriptedAgents(sm, engine, gateway())
    play(agents)
    return ScenarioRun(number, title, agents.state, agents.trace())


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
        conn.execute(text("TRUNCATE audit_log, approvals, executions, cases RESTART IDENTITY"))
    owner.dispose()
    return create_engine(os.environ["TEST_DATABASE_URL"])


def main() -> int:
    engine = _prepare_test_database()
    try:
        sm = build_state_manager(engine)
        print("\n\n".join(render(run_scenario(number, sm, engine)) for number in SCENARIOS))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
