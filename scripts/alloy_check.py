"""Run the Alloy checks of the §3 state machine and write docs/alloy/RESULTS.md (docs/alloy/README.md).

    python scripts/alloy_check.py

Needs Java 17+ and tools/org.alloytools.alloy.dist.jar (Alloy 6.2.0, not committed - see the
README for the download). Standard library only, so it runs on the host, beside Java.

Every command's expected outcome is listed below; the script exits non-zero if any differs, so a
change that breaks a property - or makes the one expected counterexample disappear - is caught.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOY_DIR = ROOT / "docs" / "alloy"
JAR = ROOT / "tools" / "org.alloytools.alloy.dist.jar"
CHECKS = ALLOY_DIR / "fsm_checks.als"
PLAN = ALLOY_DIR / "fsm_plan.als"
MODEL = ALLOY_DIR / "fsm_model.als"
# Each command file and the directory Alloy writes its results to.
SOURCES = [(CHECKS, ALLOY_DIR / "out"), (PLAN, ALLOY_DIR / "out_plan")]
RESULTS = ALLOY_DIR / "RESULTS.md"

# check: "holds" (no counterexample) | "counterexample"; run: "instance" (reachable).
EXPECTED_COUNTEREXAMPLES = {"C1_AutoCompletionNeedsReady_GuardsAbstracted", "C3_PlanBeforePlanning_GuardsAbstracted",
                            "P1_NoDeliveryBeforeReady_OldCanAdvance"}
# Commands whose trace the report prints step by step.
TRACES = ["C1_AutoCompletionNeedsReady_GuardsAbstracted", "C3_PlanBeforePlanning_GuardsAbstracted",
          "E1_AutomaticCompletion",
          "E2_MedicalQuestionClosedByStaff", "E3_MissingDocumentThenReclassified",
          "P1_NoDeliveryBeforeReady_OldCanAdvance", "P4_Scenario1UnderPlanGuards"]

LOOP_LEGEND = "`↺` = המקום שאליו המסלול חוזר אחרי הצעד האחרון: Alloy 6 מייצג כל מסלול כאינסופי, כלולאה."

HEBREW = {
    "A1_NoDeadEnd": "לכל מצב שאינו סופי יש יציאה - פנייה אינה נתקעת",
    "A2_TerminalHasNoExit": "אין שום מעבר שיוצא ממצב סופי",
    "A3_MedicalQuestionGoesToHuman": "T5: שאלה רפואית עוברת תמיד לאדם",
    "A4_EveryEscalationNamesItsKind": "כל כניסה לבקרה אנושית נושאת סיבה (escalation_kind)",
    "A5_ApprovalNeedsAKind": "אישור צוות שמחזיר לאוטומציה דורש סוג הסלמה מסוים",
    "B1_T11_TerminalIsFinal": "T11: ממצב סופי עוברים רק לאותו מצב סופי",
    "B2_ReviewAlwaysCarriesAKind": "פנייה בבקרה אנושית תמיד נושאת את סיבתה",
    "B3_LeaveReviewOnlyByStaff": "רק החלטת צוות מוציאה פנייה מבקרה אנושית",
    "B4_NoAutomationAfterMedicalDeniedOrUnsafe": "שאלה רפואית, דחיית מדיניות או הסלמת בטיחות אינן חוזרות לאוטומציה",
    "B5_ValidatedBeforeClassification": "אין סיווג לפני בקשה מאומתת",
    "B7_CallsOnlyAfterPolicyAllowed": "T1: קריאה חיצונית מתחילה רק מאישור מדיניות",
    "B8_T8_ReadyOnlyThroughReadiness": "T8: Ready רק דרך בדיקת מוכנות שעברה",
    "C1_AutoCompletionNeedsReady_GuardsAbstracted": "השלמה אוטומטית עוברת דרך Ready - בלי השער delivery_action",
    "C2_AutoCompletionNeedsReady_WithDeliveryGuard": "השלמה אוטומטית עוברת דרך Ready - עם השער delivery_action",
    "C3_PlanBeforePlanning_GuardsAbstracted": "אין תכנון בלי תוכנית שלמה - בלי השער ReadinessInProgress",
    "C4_PlanBeforePlanning_WithReadinessGuard": "אין תכנון בלי תוכנית שלמה - עם השער ReadinessInProgress",
    "E1_AutomaticCompletion": "תרחיש 1: השלמה אוטומטית דרך מוכנות",
    "E2_MedicalQuestionClosedByStaff": "תרחיש 2: שאלה רפואית נסגרת על ידי צוות",
    "E3_MissingDocumentThenReclassified": "מסמך חסר, המטופל מעלה, הפנייה מסווגת מחדש (T10)",
    "P1_NoDeliveryBeforeReady_OldCanAdvance": "אין שליחה למטופל לפני Ready - עם CanAdvance הישן (הבאג)",
    "P2_NoDeliveryBeforeReady_CanAdvanceNow": "אין שליחה למטופל לפני Ready - עם CanAdvance המתוקן (תיקון 99)",
    "P3_PlanBeforePlanning_WithPlanGuards": "אין תכנון בלי תוכנית - מול השערים הממודלים, בלי הנחה",
    "P4_Scenario1UnderPlanGuards": "תרחיש 1 עדיין אפשרי תחת השערים הממודלים (השערים אינם ריקים)",
}


def alloy(*args: str) -> str:
    # Relative to cwd: Java on Windows mangles a non-ASCII absolute path (this repo's lives under
    # a Hebrew folder name) - the relative one is plain ASCII.
    jar = os.path.relpath(JAR, ALLOY_DIR)
    result = subprocess.run(["java", "-jar", jar, *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", cwd=ALLOY_DIR)
    if result.returncode != 0:
        sys.exit(f"alloy {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def trace(command: str, source: Path) -> list[tuple[str, str, str, bool]]:
    """(state, escalation, last row's event, is_loop_start) per step of `command`'s instance."""
    text = alloy("exec", "-q", "-c", command, "-t", "text", "-o", "-", source.name)
    steps, current, loop = [], {}, False
    rows = row_events()

    def flush():
        if current:
            last = current.get("last", "")
            state = current.get("state", "Initial") or "Initial"
            if current.get("step"):  # fsm_plan.als: where the plan pointer is
                state = f"{state} · {current['step'].replace('_', ' ', 1)}"
            steps.append((state, current.get("escalation", "") or "-", rows.get(last, last) or "-", loop))

    for line in text.splitlines():
        header = re.match(r"-+State (\d+)( \(loop\))?-+", line)
        if header:
            flush()
            current, loop = {}, bool(header.group(2))
            continue
        field = re.match(r"(?:fsm_model/Case|this/Plan|fsm_plan/Plan)<:(state|escalation|last|step)=\{(.*)\}", line.strip())
        if field:
            value = field.group(2).strip()
            # "Case$0->fsm_model/Received$0" or "Plan$0->S2_CheckDocuments$0": the atom after the arrow.
            atom = value.split("->")[-1]
            current[field.group(1)] = re.sub(r"^.*/|\$\d+$", "", atom) if value else ""
    flush()
    return steps


def row_events() -> dict[str, str]:
    """R01 -> 'REQUEST_SUBMITTED (Initial -> Received)', from the generated model's row comments."""
    found = {}
    for line in MODEL.read_text(encoding="utf-8").splitlines():
        match = re.match(r"-- (R\d\d): (\S+) --(\S+)--> (\S+)", line)
        if match:
            found[match.group(1)] = f"{match.group(1)} {match.group(3)}"
    return found


def mermaid() -> str:
    """The model's rows as a state diagram - one arrow per (source, target), its events joined,
    so the many ways into human review read as one arrow each instead of a fan of 15."""
    edges: dict[tuple[str, str], list[str]] = {}
    for line in MODEL.read_text(encoding="utf-8").splitlines():
        match = re.match(r"-- (R\d\d): (\S+) --(\S+)--> (\S+)", line)
        if not match:
            continue
        _, source, event, target = match.groups()
        source = "[*]" if source == "Initial" else source
        events = edges.setdefault((source, target), [])
        if event not in events:
            events.append(event)
    lines = ["stateDiagram-v2", "    direction LR"]
    lines += [f"    {source} --> {target}: {' / '.join(events)}" for (source, target), events in edges.items()]
    lines += ["    Completed --> [*]", "    Failed --> [*]"]
    return "\n".join(lines)


def main() -> int:
    if not JAR.exists():
        sys.exit(f"missing {JAR.relative_to(ROOT)} - see docs/alloy/README.md")
    version = f"Alloy {alloy('version').strip().splitlines()[0]}"
    started = time.perf_counter()
    commands, source_of = {}, {}
    for source, out in SOURCES:
        alloy("exec", "-q", "-f", "-t", "json", "-o", out.name, source.name)
        receipt = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
        for name, command in receipt["commands"].items():
            commands[name], source_of[name] = command, source
    elapsed = time.perf_counter() - started

    rows, failures = [], []
    for name, command in commands.items():
        found = bool(command.get("solution"))
        kind = command["type"]
        if kind == "check":
            actual = "counterexample" if found else "holds"
            expected = "counterexample" if name in EXPECTED_COUNTEREXAMPLES else "holds"
        else:
            actual, expected = ("instance" if found else "no instance"), "instance"
        ok = actual == expected
        if not ok:
            failures.append(name)
        scope = re.search(r"for (.*)$", command["source"]).group(1)
        rows.append((name, kind, HEBREW.get(name, name.replace("D_reach_", "הגעה למצב ")), scope, actual, ok))

    traces = {name: trace(name, source_of[name]) for name in TRACES}
    checks = [r for r in rows if r[1] == "check"]
    runs = [r for r in rows if r[1] == "run"]
    mark = {"holds": "✅ מתקיים", "counterexample": "⚠️ דוגמה נגדית", "instance": "✅ נמצא", "no instance": "❌ לא נמצא"}

    out = [
        "# תוצאות Alloy - מכונת המצבים של סעיף 3",
        "",
        "<!-- GENERATED by scripts/alloy_check.py - do not edit by hand -->",
        "",
        f"- **מתי:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- **כלי:** {version}, solver `{receipt.get('solver', 'sat4j')}`",
        "- **מודל:** `fsm_model.als`, נוצר אוטומטית מ־`backend/hospital_agent/fsm.py` "
        "(41 שורות סעיף 3 + 3 שורות הרחבה, 13 מצבים, 14 סוגי הסלמה)",
        "- **תכונות:** `fsm_checks.als`; שכבת מיקום התוכנית והשערים שקוראים אותו: `fsm_plan.als`",
        f"- **{len(checks)} בדיקות, {len(runs)} חיפושי מסלול, {elapsed:.0f} שניות סה\"כ.** "
        f"{'כל התוצאות כצפוי.' if not failures else 'תוצאות שלא כצפוי: ' + ', '.join(failures)}",
        "",
        "**איך לקרוא:** `check` - Alloy מחפש דוגמה נגדית בכל מסלול עד גבול הצעדים; \"מתקיים\" = "
        "אין אף מסלול שמפר את התכונה, לכל תוצאה של השערים (השערים אינם ממודלים). `run` - Alloy "
        "מחפש מסלול אחד; \"נמצא\" = המצב או התרחיש בר־השגה.",
        "",
        "## בדיקות (check)",
        "",
        "| # | תכונה | היקף | תוצאה | כצפוי |",
        "|---|---|---|---|---|",
    ]
    out += [f"| `{name}` | {hebrew} | `{scope}` | {mark[actual]} | {'✓' if ok else '✗'} |"
            for name, _, hebrew, scope, actual, ok in checks]
    out += [
        "",
        "**C1 ו־C2 יחד:** בלי השערים, Alloy מוצא מסלול שבו פנייה מגיעה ל־Delivering ישירות מ־Planning "
        "ונסגרת בלי לעבור דרך Ready - כלומר הטבלה לבדה אינה מבטיחה זאת. C2 מוסיף את החוזה של "
        "השער `delivery_action` (צעד המסירה מושג רק דרך `DELIVERY_PLANNED`, שיוצא מ־Ready) - ואז "
        "התכונה מתקיימת. זו הוכחה שהבטיחות כאן נשענת על השער, ושהשער מספיק.",
        "",
        "**C3 ו־C4 - ממצא ש־Alloy העלה בעצמו:** \"אין תכנון בלי תוכנית שלמה\" נכתב במקור כבדיקה "
        "רגילה, ו־Alloy מצא לה דוגמה נגדית: Classifying → (R05) AssessingReadiness → Ready → "
        "DELIVERY_PLANNED → Planning, בלי `PLAN_CREATED` בדרך. השורה R05 היא שורת הסיווג־מחדש, "
        "והשער שלה `ReadinessInProgress` מתקיים רק כשלפנייה כבר יש תוכנית ונתוני מוכנות - כלומר "
        "אחרי שנבנתה תוכנית. C4 מוסיף את החוזה של השער, והתכונה מתקיימת. גם כאן: הטבלה לבדה אינה "
        "מספיקה, השער כן.",
        "",
        "**P1–P4 - הבאג ש־Alloy מצא, והתיקון (תיקון 99):** `fsm_plan.als` ממדל את מיקום המצביע "
        "בתוכנית ואת השערים שקוראים אותו, כפי שהם כתובים ב־`guards.py` - כך שהחוזה של C2 כבר "
        "אינו הנחה אלא נבדק. P1 מריץ את `CanAdvance` כפי שהיה (רק \"יש צעד הבא\") ו־Alloy מוצא "
        "את הבאג: `PLAN_CREATED` ואז שלושה `STEP_ADVANCED` מביאים את התוכנית לצעד המסירה, "
        "ו־`POLICY_ALLOWED` מעביר ל־Delivering - בלי שליפת נתונים ובלי בדיקת מוכנות. הבדיקה "
        "שוחזרה גם מול ה־State Manager האמיתי. P2 מריץ את `CanAdvance` המתוקן - `STEP_ADVANCED` "
        "נשאר בתוך שלב השליפה, וצעד המסירה מושג רק ב־`DELIVERY_PLANNED` מ־Ready - והתכונה "
        "מתקיימת. P4 מוודא שהשערים לא חוסמים את התרחיש התקין, כלומר ש־P2 ו־P3 אינם מתקיימים "
        "רק משום ששום דבר לא יכול לקרות.",
        "",
        "## הגעה למצבים ותרחישים (run)",
        "",
        "| # | מה | היקף | תוצאה | כצפוי |",
        "|---|---|---|---|---|",
    ]
    out += [f"| `{name}` | {hebrew} | `{scope}` | {mark[actual]} | {'✓' if ok else '✗'} |"
            for name, _, hebrew, scope, actual, ok in runs]
    for name, steps in traces.items():
        out += ["", f"### מסלול: `{name}`", "", HEBREW.get(name, name), "", LOOP_LEGEND, "",
                "| צעד | מצב | escalation_kind | השורה שהביאה לכאן |", "|---|---|---|---|"]
        out += [f"| {i}{' ↺' if loop else ''} | {state} | {esc} | {event} |"
                for i, (state, esc, event, loop) in enumerate(steps)]
    out += ["", "## תרשים המצבים (מתוך המודל)", "", "```mermaid", mermaid(), "```", ""]
    RESULTS.write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(f"{len(checks)} checks, {len(runs)} runs, {elapsed:.0f}s - "
          f"{'all as expected' if not failures else 'UNEXPECTED: ' + ', '.join(failures)}; wrote {RESULTS.relative_to(ROOT)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
