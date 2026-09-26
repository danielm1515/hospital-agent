"""Sub-project 18 (design D12): the Approved Source Registry check the instruction routes use.

Fix round 1 (I1): `is_approved` now delegates entirely to the real OPA (`opa_runner`) - there is
one implementation of "is this source approved right now", never a second Python
reimplementation of `policy.rego`'s own time parsing. This file both exercises `is_approved`
against the real, production registry, and proves - for every real entry plus several synthetic
"looks like a date but isn't RFC3339" ones - that it agrees with `opa_runner.instruction_source_approved`
asked directly, i.e. that the two names really are the same code path.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from hospital_agent.instruction_registry import is_approved
from hospital_agent.policy import opa_runner
from hospital_agent.policy.opa_runner import DATA_DIR, instruction_source_approved

_REGISTRY = json.loads((DATA_DIR / "approved_instruction_sources.json").read_text(encoding="utf-8"))
REAL_ENTRIES: dict = _REGISTRY["hospital_agent"]["approved_instruction_sources"]

# Fix round 1 (I1, M1): several date shapes that look plausible but are not RFC3339 - a space
# separator, a "basic" (no separators) form, no seconds, a numeric (non-colon) UTC offset, an
# ISO week date, and (M1) a naive valid_until with no offset at all. `time.parse_rfc3339_ns`
# rejects all six, which makes `instruction_source_approved` undefined - never approved.
ODD_FORMATS: dict = {
    "SPACE_SEPARATOR": {"approved": True, "version": "1",
                       "valid_from": "2024-01-01 00:00:00Z", "valid_until": "2099-01-01T00:00:00Z"},
    "BASIC_FORMAT": {"approved": True, "version": "1",
                    "valid_from": "20240101T000000Z", "valid_until": "2099-01-01T00:00:00Z"},
    "MISSING_SECONDS": {"approved": True, "version": "1",
                       "valid_from": "2024-01-01T00:00Z", "valid_until": "2099-01-01T00:00:00Z"},
    "NUMERIC_OFFSET": {"approved": True, "version": "1",
                      "valid_from": "2024-01-01T00:00:00+0300", "valid_until": "2099-01-01T00:00:00Z"},
    "WEEK_DATE": {"approved": True, "version": "1",
                 "valid_from": "2024-W01-1T00:00:00Z", "valid_until": "2099-01-01T00:00:00Z"},
    "NAIVE_VALID_UNTIL": {"approved": True, "version": "1",
                         "valid_from": "2024-01-01T00:00:00Z", "valid_until": "2099-01-01T00:00:00"},
}


@pytest.fixture
def merged_data_dir(tmp_path):
    """Every real registry entry plus the odd-format synthetic ones above, in a throwaway
    DATA_DIR - never the production file itself."""
    merged = {"hospital_agent": {"approved_instruction_sources": {**REAL_ENTRIES, **ODD_FORMATS}}}
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "approved_instruction_sources.json").write_text(json.dumps(merged), encoding="utf-8")
    return data_dir


# --- is_approved() against the real, production registry -----------------------------------

def test_an_approved_and_current_source_is_approved():
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3") is True


def test_an_unlisted_source_is_not_approved():
    assert is_approved("INSTR-NO-SUCH-THING", "1") is False


def test_the_wrong_version_is_not_approved():
    assert is_approved("INSTR-PREP-COLONOSCOPY", "99") is False


def test_an_expired_source_is_not_approved():
    # INSTR-RETIRED-2025: valid_until 2025-12-31 - long past.
    assert is_approved("INSTR-RETIRED-2025", "1") is False


def test_a_not_yet_valid_source_is_not_approved():
    # INSTR-DRAFT-2090: valid_from 2090-01-01 - long from now.
    assert is_approved("INSTR-DRAFT-2090", "1") is False


def test_an_empty_version_is_not_approved():
    """Rego's own nonempty(version) check (fix round 1 I1: still enforced, now by OPA itself,
    not a Python re-check)."""
    assert is_approved("INSTR-PREP-COLONOSCOPY", "") is False


# --- fail closed when OPA itself cannot be asked --------------------------------------------

def test_missing_binary_fails_closed():
    assert instruction_source_approved("INSTR-PREP-COLONOSCOPY", "3", opa_binary="/nonexistent/opa") is False


def test_timeout_fails_closed():
    assert instruction_source_approved("INSTR-PREP-COLONOSCOPY", "3", timeout=0.000001) is False


def test_unreadable_output_fails_closed(monkeypatch):
    class Done:
        returncode, stdout = 0, "not json"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    assert instruction_source_approved("INSTR-PREP-COLONOSCOPY", "3") is False


def test_an_undefined_rule_fails_closed(monkeypatch):
    """opa eval --format json prints a bare {} (no "result" key at all) when a partial rule
    like instruction_source_approved is undefined for the given input - this must be treated
    exactly like any other unreadable answer, never crash."""
    class Done:
        returncode, stdout = 0, "{}"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    assert instruction_source_approved("INSTR-PREP-COLONOSCOPY", "3") is False


def test_a_non_true_answer_fails_closed(monkeypatch):
    class Done:
        returncode = 0
        stdout = '{"result": [{"expressions": [{"value": "yes"}]}]}'  # not literally true

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    assert instruction_source_approved("INSTR-PREP-COLONOSCOPY", "3") is False


# --- I1: every real entry, plus the odd-format ones, agree between is_approved() and OPA ---

@pytest.mark.parametrize("source_id", sorted(REAL_ENTRIES))
def test_every_real_registry_entry_agrees_with_opa(monkeypatch, merged_data_dir, source_id):
    monkeypatch.setattr(opa_runner, "DATA_DIR", merged_data_dir)
    version = REAL_ENTRIES[source_id]["version"]
    # The two currently non-current real entries (see the dedicated tests above); every other
    # real entry is approved: true and within its wide 2026..2030 validity window right now.
    expected = source_id not in ("INSTR-RETIRED-2025", "INSTR-DRAFT-2090")
    assert is_approved(source_id, version) is expected
    assert instruction_source_approved(source_id, version) is expected


@pytest.mark.parametrize("source_id", sorted(ODD_FORMATS))
def test_every_odd_date_format_is_denied(monkeypatch, merged_data_dir, source_id):
    monkeypatch.setattr(opa_runner, "DATA_DIR", merged_data_dir)
    assert is_approved(source_id, "1") is False
    assert instruction_source_approved(source_id, "1") is False


def test_the_real_registry_file_exists_and_parses():
    assert (DATA_DIR / "approved_instruction_sources.json").exists()
    assert "INSTR-NEURO-VISIT" in REAL_ENTRIES
