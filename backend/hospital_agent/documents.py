"""The sub-project 11 document catalog's Hebrew labels (design §2) - the one shared place in
the backend, so the status message (`llm/message.py`) and anything else that shows a document
type do not each keep their own copy.
"""
from __future__ import annotations

CATALOG_LABELS: dict[str, str] = {
    "CBC": "ספירת דם מלאה",
    "COAGULATION_TESTS": "בדיקות קרישה",
    "ECG": "תרשים פעילות חשמלית של הלב",
    "URINALYSIS": "בדיקת שתן",
    "PREOP_SUMMARY": "סיכום טרום ניתוח",
}


def document_label(code: str) -> str:
    """The catalog's Hebrew label for `code`, or the code itself when this version does not
    know it (e.g. the demo mock's `referral` / `blood_test` - never invented)."""
    return CATALOG_LABELS.get(code, code)
