"""docs/alloy/fsm_model.als is generated from fsm.py and must never drift from it (docs/alloy/README.md)."""
from pathlib import Path

from hospital_agent.fsm import EXTENSION_TRANSITIONS, TRANSITIONS
from hospital_agent.policy import alloy_model

MODEL = Path(__file__).resolve().parents[2] / "docs" / "alloy" / "fsm_model.als"


def test_the_committed_alloy_model_is_exactly_what_fsm_py_renders():
    assert MODEL.read_text(encoding="utf-8").replace("\r\n", "\n") == alloy_model.render(), (
        "fsm.py changed: regenerate with "
        "`docker compose run --rm backend python -m hospital_agent.policy.alloy_model > docs/alloy/fsm_model.als`")


def test_every_row_of_the_table_is_a_row_of_the_model():
    rendered = alloy_model.render()
    rows = TRANSITIONS + EXTENSION_TRANSITIONS
    assert rendered.count("extends Row {}") == len(rows) == 44
    for index, row in enumerate(rows, 1):
        block = rendered.split(f"one sig R{index:02d} extends Row {{}} {{", 1)[1].split("}", 1)[0]
        assert f"ev = {row.event.value}" in block and f"dst = {row.target.value}" in block
        assert (f"src = {row.source.value}" if row.source else "no src") in block


def test_an_escalation_row_lists_exactly_its_kinds():
    rendered = alloy_model.render()
    block = rendered.split("one sig R07 extends Row {} {", 1)[1].split("}", 1)[0]
    assert "kinds = ClassificationFailed + SafetyEscalation + TemporalViolation" in block
