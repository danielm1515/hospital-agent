"""Sub-project 18 (design D12, D14; docs/spec_corrections.md rows 89-90): whether the Approved
Source Registry - the same JSON file OPA reads out of its policy bundle
(`policy/data/approved_instruction_sources.json`, `data.hospital_agent.approved_instruction_sources`)
- approves one `source_id` + `version` right now.

Read directly from disk, not through OPA: the instruction routes this backs
(`api/instructions.py`) are outside the FSM and never build an OPA input, but they must apply
exactly OPA's own rule (`instruction_source_approved`, `policy.rego`) - `approved == true`, the
same `version`, and now inside `[valid_from, valid_until)` - never trusting the
appointment-service's own answer to approve itself. `opa_runner.DATA_DIR` is the same directory
OPA is pointed at (`opa eval --data <DATA_DIR> ...`), so a change to the bundle is picked up by
both readers together.
"""
from __future__ import annotations

import json
from datetime import datetime

from .policy.opa_runner import DATA_DIR

REGISTRY_FILE = DATA_DIR / "approved_instruction_sources.json"


def is_approved(source_id: str, version: str, *, now: datetime) -> bool:
    """Mirrors `policy.rego`'s `instruction_source_approved` rule exactly: `approved == true`,
    the same `version`, and `valid_from <= now < valid_until`. Any read or shape failure is
    "not approved", never an exception - a missing or corrupt registry file must fail closed,
    not turn into a 500."""
    try:
        raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    package = raw.get("hospital_agent")
    entries = package.get("approved_instruction_sources") if isinstance(package, dict) else None
    entry = entries.get(source_id) if isinstance(entries, dict) else None
    if not isinstance(entry, dict) or entry.get("approved") is not True or entry.get("version") != version:
        return False
    valid_from, valid_until = entry.get("valid_from"), entry.get("valid_until")
    if not isinstance(valid_from, str) or not isinstance(valid_until, str):
        return False
    try:
        parsed_from = datetime.fromisoformat(valid_from)
        parsed_until = datetime.fromisoformat(valid_until)
    except ValueError:
        return False
    if parsed_from.tzinfo is None or parsed_until.tzinfo is None:
        return False
    return parsed_from <= now < parsed_until
