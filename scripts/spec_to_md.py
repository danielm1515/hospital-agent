"""Convert Hospital_Agent_Clean.docx into per-section Markdown files under docs/spec/.

Spec section N is written to docs/spec/NN-<slug>.md; docs/spec/README.md holds the
preamble and an index. Output is generated -- edit the docx and re-run, don't hand-edit.

Usage (from repo root):  python scripts/spec_to_md.py   (requires python-docx)
"""
import re
import sys
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Hospital_Agent_Clean.docx"
OUT = ROOT / "docs" / "spec"

SLUGS = {
    0: "scenarios", 1: "components", 2: "states-events", 3: "transitions-guards",
    4: "scenario-diagrams", 5: "action-registry", 6: "temporal-rules",
    7: "temporal-monitor", 8: "opa-policy", 9: "z3", 10: "prolog", 11: "datalog",
    12: "context-audit-approval", 13: "invariants", 14: "fail-closed",
    15: "golden-traces", 16: "tests", 17: "naming-conventions",
    18: "operational-assumptions",
}

HEB = re.compile(r"[\u0590-\u05FF]")


def clean(s: str) -> str:
    return s.replace("\u200e", "").replace("\u200f", "").replace("\xa0", " ").rstrip()


def cell_text(c) -> str:
    t = "<br>".join(clean(p.text).strip() for p in c.paragraphs if clean(p.text).strip())
    return t.replace("|", "\\|")


def table_md(t: Table) -> str:
    rows = []
    for r in t.rows:
        seen, cells = set(), []
        for c in r.cells:  # merged cells repeat the same _tc
            if id(c._tc) in seen:
                continue
            seen.add(id(c._tc))
            cells.append(cell_text(c))
        rows.append(list(reversed(cells)))  # RTL table stored right-to-left
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def is_code(p: Paragraph) -> bool:
    fonts = {r.font.name for r in p.runs if r.text.strip()}
    return bool(fonts) and fonts <= {"Consolas", "Courier New"}


def is_caption(line: str) -> bool:
    s = line.strip()
    return bool(s) and bool(HEB.match(s[0]))


def lang_of(lines):
    first = next((l.strip() for l in lines if l.strip()), "")
    body = "\n".join(lines)
    if first.startswith("stateDiagram"):
        return "mermaid"
    if first.startswith("package ") or "import rego" in body:
        return "rego"
    if first.startswith(("from ", "import ", "def ")):
        return "python"
    if first.startswith(("//", "{")):
        return "jsonc" if "//" in first else "json"
    if ":-" in body or first.startswith(("?-", "%")) or re.match(r"^[a-z_]+\(", first):
        return "prolog"
    return "text"


SPLIT_BEFORE = re.compile(r"^(// input|// output|\{\"hospital_agent\"|flows\.dl|SCENARIO )")


def code_md(lines):
    """Split a run of code paragraphs into fenced blocks + caption paragraphs."""
    blocks, cur = [], []

    def flush():
        while cur and not cur[-1].strip():
            cur.pop()
        if cur:
            lang = lang_of(cur)
            if lang == "mermaid":  # source indentation is inconsistent
                cur[1:] = ["    " + l.strip() for l in cur[1:]]
            blocks.append(f"```{lang}\n" + "\n".join(cur) + "\n```")
        cur.clear()

    for line in lines:
        if is_caption(line):
            flush()
            blocks.append(f"**{line.strip()}**")
        elif SPLIT_BEFORE.match(line.strip()):
            flush()
            cur.append(line)
        else:
            cur.append(line)
    flush()
    return "\n\n".join(blocks)


def main():
    d = docx.Document(str(SRC))
    sections = {-1: []}
    sec = -1
    code_buf = []

    def emit(s):
        sections[sec].append(s)

    def flush_code():
        if code_buf:
            emit(code_md(code_buf))
            code_buf.clear()

    for child in d.element.body.iterchildren():
        tag = child.tag.split("}")[1]
        if tag == "tbl":
            flush_code()
            emit(table_md(Table(child, d)))
            continue
        if tag != "p":
            continue
        p = Paragraph(child, d)
        text = clean(p.text)
        if not text.strip():
            continue
        if is_code(p):
            code_buf.append(text)
            continue
        flush_code()
        style = p.style.name
        text = text.strip()
        if style == "Title":
            emit(f"# {text}")
        elif style == "Heading 1":
            sec = int(text.split(".")[0])
            sections[sec] = [f"# {text}"]
        elif style == "Heading 2":
            emit(f"## {text}")
        elif style == "List Paragraph":
            emit(f"- {text}")
        else:
            emit(text)
    flush_code()

    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for n, parts in sections.items():
        if n < 0:
            continue
        # join consecutive list items without blank lines
        md = ""
        for i, part in enumerate(parts):
            sep = "\n" if (i and part.startswith("- ") and parts[i - 1].startswith("- ")) else "\n\n"
            md += (sep if i else "") + part
        name = f"{n:02d}-{SLUGS[n]}.md"
        (OUT / name).write_text(GENERATED + md + "\n", encoding="utf-8")
        subs = [p[3:] for p in parts if p.startswith("## ")]
        index.append(f"- [{parts[0][2:]}]({name})" + (f" — {' · '.join(subs)}" if subs else ""))
    readme = (GENERATED + "\n\n".join(sections[-1]) + "\n\n## תוכן\n\n" + "\n".join(index) + "\n")
    (OUT / "README.md").write_text(readme, encoding="utf-8")
    print("sections:", sorted(k for k in sections if k >= 0))


GENERATED = "<!-- Generated by scripts/spec_to_md.py from Hospital_Agent_Clean.docx. Do not edit. -->\n\n"


if __name__ == "__main__":
    sys.exit(main())
