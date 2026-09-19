"""Consistency between the control layers, proved with Z3 (spec §9.2).

Seven abstract properties in nine queries; every query must be UNSAT. The layer
encodings are the spec's, unchanged (scoped snapshots of automatic actions; the
engines' real decisions are compared in integration tests).

    docker compose run --rm backend python -m hospital_agent.policy.consistency
"""
from __future__ import annotations

import sys

from z3 import And, Bools, Implies, Not, Or, Solver, unsat

# Abstract inputs from trusted services.
(identity, ctx, in_plan, known, medical, approval_ok, minimized, high_risk,
 policy_override_ok, attempts_exhausted, evaluated, patient_facing,
 load_instructions, source_approved) = Bools(
    "identity ctx in_plan known medical approval_ok minimized high_risk "
    "policy_override_ok attempts_exhausted evaluated patient_facing "
    "load_instructions source_approved")

# OPA subset for these probes; not a complete translation of policy.rego.
opa_deny = Or(Not(identity), Not(ctx), Not(in_plan), Not(known),
              And(medical, Not(approval_ok)), Not(minimized),
              attempts_exhausted,                            # attempts_exhausted, INV-4
              And(patient_facing, Not(evaluated)),           # message_not_evaluated, INV-11
              And(load_instructions, Not(source_approved)))  # unapproved_instruction_source
opa_allow = And(Not(opa_deny), Or(Not(high_risk), policy_override_ok))
# Prolog automatic-action slice, including the output evaluation gate.
prolog_blocked = Or(And(medical, Not(approval_ok)), Not(in_plan), Not(ctx),
                    And(patient_facing, Not(evaluated)))
prolog_allowed = And(Not(prolog_blocked), identity, ctx)
# Datalog field-target allowlist.
datalog_safe = minimized
# Temporal: snapshot abstraction of T2, T3, T4 and the content gate of T6
temporal_ok = And(identity, ctx, in_plan, Implies(medical, approval_ok))

QUERIES = [
    ("P1", "OPA allows what Prolog blocks", And(opa_allow, Not(prolog_allowed))),
    ("P1", "Prolog allows what Temporal forbids", And(prolog_allowed, Not(temporal_ok))),
    ("P1", "OPA allows an unminimized flow", And(opa_allow, Not(datalog_safe))),
    ("P2", "medical answer without approval", And(medical, Not(approval_ok), Or(opa_allow, prolog_allowed))),
    ("P3", "execution without patient context", And(Not(ctx), Or(opa_allow, prolog_allowed, temporal_ok))),
    ("P4", "high-risk action without policy override", And(high_risk, Not(policy_override_ok), opa_allow)),
    ("P5", "allow after attempts exhausted", And(attempts_exhausted, opa_allow)),
    ("P6", "unevaluated patient-facing output", And(patient_facing, Not(evaluated), opa_allow)),
    ("P7", "unapproved instruction source", And(load_instructions, Not(source_approved), opa_allow)),
]


def check() -> list[tuple[str, str, str]]:
    """(property, description, Z3 result) for each of the nine queries."""
    s = Solver()
    s.set(timeout=5000)
    results = []
    for prop, description, formula in QUERIES:
        s.push()
        s.add(formula)
        results.append((prop, description, str(s.check())))
        s.pop()
    return results


def main() -> int:
    failed = [r for r in check() if r[2] != str(unsat)]
    for prop, description, result in failed:
        print(f"{prop} FAILED ({result}): {description}")
    if failed:
        return 1
    print("7 abstract properties passed (9 UNSAT queries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
