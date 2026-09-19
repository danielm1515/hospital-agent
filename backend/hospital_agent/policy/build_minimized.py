"""Export minimized/2 and external/1 from flows.dl to the OPA data file (spec §11).

    flows.dl  --(this build step)-->  data/minimized_fields.json  --(opa data)-->  policy.rego

Every external/1 target gets an entry, including targets with no allowed field
(instruction_system: []). Run after changing flows.dl:

    docker compose run --rm backend python -m hospital_agent.policy.build_minimized
"""
from __future__ import annotations

import json
from pathlib import Path

from .datalog import Datalog

OUTPUT = Path(__file__).with_name("data") / "minimized_fields.json"


def export(datalog: Datalog | None = None) -> dict:
    datalog = datalog or Datalog()
    minimized = datalog.relation("minimized")
    fields = {
        target: sorted(field for field, to in minimized if to == target)
        for (target,) in datalog.relation("external")
    }
    return {"hospital_agent": {"minimized_fields": fields}}


def render(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def main() -> None:
    OUTPUT.write_text(render(export()), encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
