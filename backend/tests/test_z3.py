"""Z3: readiness feasibility (spec §9.1) and cross-layer consistency (spec §9.2)."""
import subprocess
import sys

import pytest

from hospital_agent.policy import consistency
from hospital_agent.policy.readiness import ask_patient_is_safe


@pytest.mark.parametrize("hours_until, result", [(96, "unsat"), (32, "unsat"), (30, "unsat"), (20, "sat")])
def test_spec_9_1_examples(hours_until, result):
    """§9.1: 96 -> True, 32 -> True, 20 -> False. The legal maximum is 30 hours; equality is allowed."""
    verdict = ask_patient_is_safe(hours_until)
    assert (verdict.safe, verdict.result) == (result == "unsat", result)


def test_sat_carries_the_counterexample():
    verdict = ask_patient_is_safe(20)
    assert "upload_h" in verdict.detail and "doc_type" in verdict.detail


def test_29_hours_is_not_enough():
    assert ask_patient_is_safe(29).result == "sat"


@pytest.mark.parametrize("bad", [-1, float("inf"), float("nan"), "96", None, True])
def test_invalid_deadline_escalates(bad):
    verdict = ask_patient_is_safe(bad)
    assert (verdict.safe, verdict.result) == (False, "invalid_deadline")


def test_spec_9_2_all_nine_queries_are_unsat():
    results = consistency.check()
    assert len(results) == 9 and {prop for prop, _, _ in results} == {f"P{i}" for i in range(1, 8)}
    assert all(result == "unsat" for _, _, result in results), results


def test_spec_9_2_script_prints_the_documented_line():
    out = subprocess.run([sys.executable, "-m", "hospital_agent.policy.consistency"],
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == "7 abstract properties passed (9 UNSAT queries)"
