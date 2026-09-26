"""Sub-project 18 (design D12): the Approved Source Registry check the instruction routes use -
must mirror policy.rego's instruction_source_approved rule exactly (approved by CLAUDE.md's
own opa_reference.py comparison, but this module never calls OPA)."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent.instruction_registry import REGISTRY_FILE, is_approved

NOW = datetime(2027, 1, 1, tzinfo=UTC)


def test_a_listed_and_current_source_is_approved():
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3", now=NOW) is True


def test_an_unlisted_source_is_not_approved():
    assert is_approved("INSTR-NO-SUCH-THING", "1", now=NOW) is False


def test_the_wrong_version_is_not_approved():
    assert is_approved("INSTR-PREP-COLONOSCOPY", "99", now=NOW) is False


def test_an_expired_source_is_not_approved():
    # INSTR-RETIRED-2025: valid_from 2024-01-01, valid_until 2025-12-31 - NOW (2027) is past it.
    assert is_approved("INSTR-RETIRED-2025", "1", now=NOW) is False


def test_a_not_yet_valid_source_is_not_approved():
    # INSTR-DRAFT-2090: valid_from 2090-01-01 - NOW (2027) is before it.
    assert is_approved("INSTR-DRAFT-2090", "1", now=NOW) is False


def test_valid_until_is_exclusive_and_valid_from_is_inclusive():
    """Matches policy.rego exactly: valid_from <= now < valid_until. INSTR-RETIRED-2025's own
    registry entry: valid_from 2024-01-01T00:00:00Z, valid_until 2025-12-31T00:00:00Z."""
    valid_from = datetime(2024, 1, 1, tzinfo=UTC)
    valid_until = datetime(2025, 12, 31, tzinfo=UTC)
    assert is_approved("INSTR-RETIRED-2025", "1", now=valid_from) is True  # inclusive
    assert is_approved("INSTR-RETIRED-2025", "1", now=valid_until - timedelta(microseconds=1)) is True
    assert is_approved("INSTR-RETIRED-2025", "1", now=valid_until) is False  # exclusive


def test_a_missing_registry_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("hospital_agent.instruction_registry.REGISTRY_FILE", tmp_path / "nope.json")
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3", now=NOW) is False


def test_a_corrupt_registry_file_fails_closed(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    monkeypatch.setattr("hospital_agent.instruction_registry.REGISTRY_FILE", bad)
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3", now=NOW) is False


@pytest.mark.parametrize("mutate", [
    lambda e: e.__setitem__("approved", False),
    lambda e: e.__setitem__("approved", "true"),  # not a boolean
    lambda e: e.pop("version"),
    lambda e: e.__setitem__("valid_from", 123),
    lambda e: e.__setitem__("valid_until", "not-a-date"),
    lambda e: e.__setitem__("valid_from", "2026-01-01T00:00:00"),  # naive
])
def test_a_malformed_entry_fails_closed(tmp_path, monkeypatch, mutate):
    registry = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    entry = registry["hospital_agent"]["approved_instruction_sources"]["INSTR-PREP-COLONOSCOPY"]
    mutate(entry)
    custom = tmp_path / "registry.json"
    custom.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr("hospital_agent.instruction_registry.REGISTRY_FILE", custom)
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3", now=NOW) is False


def test_the_real_registry_file_exists_and_parses():
    assert REGISTRY_FILE.exists()
    data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    assert "INSTR-NEURO-VISIT" in data["hospital_agent"]["approved_instruction_sources"]
