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
# P1 replays the bug Alloy found (row 99) on the guard as it was: a counterexample there is the
# evidence, and its disappearing would mean the replay no longer models the old guard.
BUG = "P1_NoDeliveryBeforeReady_OldCanAdvance"
FIX = "P2_NoDeliveryBeforeReady_CanAdvanceNow"
EXPECTED_COUNTEREXAMPLES = {BUG}
# Commands whose trace the report prints step by step.
SCENARIOS = ["E1_AutomaticCompletion", "E2_MedicalQuestionClosedByStaff", "E3_MissingDocumentThenReclassified"]
TRACES = [BUG, *SCENARIOS]

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
    "B6_CallsOnlyAfterPolicyAllowed": "T1: קריאה חיצונית מתחילה רק מאישור מדיניות",
    "B7_T8_ReadyOnlyThroughReadiness": "T8: Ready רק דרך בדיקת מוכנות שעברה",
    "E1_AutomaticCompletion": "תרחיש 1: השלמה אוטומטית דרך מוכנות",
    "E2_MedicalQuestionClosedByStaff": "תרחיש 2: שאלה רפואית נסגרת על ידי צוות",
    "E3_MissingDocumentThenReclassified": "מסמך חסר, המטופל מעלה, הפנייה מסווגת מחדש (T10)",
    "P1_NoDeliveryBeforeReady_OldCanAdvance": "אין שליחה למטופל לפני Ready - לפני התיקון",
    "P2_NoDeliveryBeforeReady_CanAdvanceNow": "אין שליחה למטופל לפני Ready (T8)",
    "P3_PlanBeforePlanning_WithPlanGuards": "אין תכנון בלי תוכנית שלמה (Unsupported לא מגיעה לשם)",
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
    by_name = {r[0]: r for r in rows}
    holding = [r for r in rows if r[1] == "check" and r[0] != BUG]
    runs = [r for r in rows if r[1] == "run"]
    mark = {"holds": "✅ מתקיים", "counterexample": "⚠️ דוגמה נגדית", "instance": "✅ נמצא", "no instance": "❌ לא נמצא"}

    def table(selected: list[tuple], head: str) -> list[str]:
        lines = [f"| # | {head} | היקף | תוצאה | כצפוי |", "|---|---|---|---|---|"]
        lines += [f"| `{name}` | {hebrew} | `{scope}` | {mark[actual]} | {'✓' if ok else '✗'} |"
                  for name, _, hebrew, scope, actual, ok in selected]
        return lines

    def steps_table(name: str) -> list[str]:
        lines = [LOOP_LEGEND, "", "| צעד | מצב · צעד בתוכנית | escalation_kind | השורה שהביאה לכאן |",
                 "|---|---|---|---|"]
        lines += [f"| {i}{' ↺' if loop else ''} | {state} | {esc} | {event} |"
                  for i, (state, esc, event, loop) in enumerate(traces[name])]
        return lines

    bug, fix = by_name[BUG], by_name[FIX]
    out = [
        "# תוצאות Alloy - מכונת המצבים של סעיף 3",
        "",
        "<!-- GENERATED by scripts/alloy_check.py - do not edit by hand -->",
        "",
        f"- **מתי:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- **כלי:** {version}, solver `{receipt.get('solver', 'sat4j')}`",
        "- **מודל:** `fsm_model.als`, נוצר אוטומטית מ־`backend/hospital_agent/fsm.py` "
        "(41 שורות סעיף 3 + 3 שורות הרחבה, 13 מצבים, 14 סוגי הסלמה); `fsm_plan.als` מוסיף את "
        "מיקום התוכנית ואת השערים שקוראים אותו, כפי שהם כתובים ב־`guards.py`",
        f"- **{len(holding)} תכונות מתקיימות, באג אחד שנמצא ותוקן, {len(runs)} חיפושי מסלול, "
        f"{elapsed:.0f} שניות.** "
        f"{'כל התוצאות כצפוי.' if not failures else 'תוצאות שלא כצפוי: ' + ', '.join(failures)}",
        "",
        "**איך לקרוא:** `check` - Alloy מחפש דוגמה נגדית בכל מסלול עד 20 צעדים; \"מתקיים\" = אין "
        "אף מסלול שמפר את התכונה. `run` - Alloy מחפש מסלול אחד; \"נמצא\" = בר־השגה.",
        "",
        "## 1. התכונות שמתקיימות",
        "",
        "A ו־B נבדקות על הטבלה בלבד, לכל תוצאה של השערים. P2 ו־P3 נבדקות מול השערים שקוראים את "
        "מיקום התוכנית, כי על הטבלה לבדה יש להן דוגמה נגדית - הן מחזיקות בזכות השערים.",
        "",
        *table(holding, "תכונה"),
        "",
        "## 2. הבאג ש־Alloy מצא - לפני ואחרי התיקון",
        "",
        "`CanAdvance` (§3.1: \"המצביע החדש נמצא בתוך התוכנית\") בדק רק שיש צעד הבא. לכן ה־State "
        "Manager אישר `PLAN_CREATED` ואחריו שלושה `STEP_ADVANCED` אל צעד המסירה, ו־`POLICY_ALLOWED` "
        "העביר את הפנייה ל־`Delivering` - קריאה לערוץ המטופל בלי שליפת נתונים ובלי בדיקת מוכנות. "
        "רק סדר הפעולות של ה־Orchestrator מנע זאת בפועל, והטסטים לא תפסו את זה כי הם מריצים את "
        "הקוד במסלולים שהוא באמת עובר - Alloy בודק כל מסלול שה־FSM מרשה. הבאג שוחזר מול "
        "ה־State Manager האמיתי ותוקן: ב־`STEP_ADVANCED` התוכנית מתקדמת רק בתוך שלב השליפה, וצעד "
        "המסירה מושג רק ב־`DELIVERY_PLANNED` מ־Ready (`spec_corrections` תיקון 99).",
        "",
        "| | `CanAdvance` | תוצאה | כצפוי |",
        "|---|---|---|---|",
        f"| **לפני** (`{BUG}`) | \"יש צעד הבא\" | {mark[bug[4]]} | {'✓' if bug[5] else '✗'} |",
        f"| **אחרי** (`{FIX}`) | בתוך שלב השליפה בלבד | {mark[fix[4]]} | {'✓' if fix[5] else '✗'} |",
        "",
        "### המסלול שמצא את הבאג (לפני התיקון)",
        "",
        *steps_table(BUG),
        "",
        "## 3. הגעה למצבים ותרחישים",
        "",
        "כל 13 המצבים ברי־השגה, ושלושת התרחישים עוברים תחת השערים כפי שהם בקוד - כלומר התכונות "
        "בסעיף 1 אינן מתקיימות רק משום שהשערים חוסמים הכול.",
        "",
        *table(runs, "מה"),
    ]
    for name in SCENARIOS:
        out += ["", f"### מסלול: `{name}`", "", HEBREW.get(name, name), "", *steps_table(name)]
    out += ["", "## 4. תרשים המצבים (מתוך המודל)", "", "```mermaid", mermaid(), "```", ""]
    RESULTS.write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(f"{len(holding)} properties hold, the bug replay {'as expected' if bug[5] else 'NOT as expected'}, "
          f"{len(runs)} runs, {elapsed:.0f}s - "
          f"{'all as expected' if not failures else 'UNEXPECTED: ' + ', '.join(failures)}; wrote {RESULTS.relative_to(ROOT)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
