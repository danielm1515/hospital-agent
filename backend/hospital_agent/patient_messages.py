"""The fixed messages a staff member may send the patient without a ContentApproval
(sub-project 15, design §7.1).

Each text is written and approved in advance; a parameter, when there is one, comes from a
closed list - there is no free text here, so no medical content can reach the patient through
a template. The patient screen shows a staff message only if it is one of these texts or a
clinical staff member's text with a consumed ContentApproval (design §7.5): is_template_text()
is that first half. The owner edits the wording here, and only here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from types import MappingProxyType

from .documents import CATALOG_LABELS

DOCUMENT_REQUEST = "document_request"

TOPICS: Mapping[str, str] = MappingProxyType({
    "appointment_time": "מועד התור",
    "required_documents": "המסמכים הנדרשים לתור",
    "preparation": "הוראות ההכנה לתור",
})


@dataclass(frozen=True)
class Template:
    template_id: str
    purpose: str  # "question" | "document" | "closing"
    text: str
    param: str | None = None  # the {placeholder} the text takes, if any
    options: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))  # the closed list: code -> Hebrew


TEMPLATES: tuple[Template, ...] = (
    Template("clarify_general", "question", "לא הצלחנו להבין את פנייתך. נשמח אם תפרט/י במה נוכל לעזור."),
    Template("clarify_did_you_mean", "question", "האם התכוונת ל{topic}? נשמח לאישור או לפירוט.", "topic", TOPICS),
    Template("clarify_appointment", "question",
             "האם הפנייה נוגעת לתור קיים? אם כן, נא לציין את התאריך או את המחלקה."),
    Template(DOCUMENT_REQUEST, "document", "נא להעלות את המסמך: {document}.", "document", MappingProxyType(dict(CATALOG_LABELS))),
    Template("close_handled", "closing", "פנייתך טופלה על ידי הצוות."),
    Template("close_out_of_scope", "closing",
             "פנייתך אינה בתחום שהמערכת מטפלת בו. לשאלות אחרות ניתן לפנות למוקד."),
    Template("close_no_reply", "closing",
             "לא התקבלה תשובה בזמן, ולכן הפנייה נסגרה. אפשר לפתוח פנייה חדשה בכל עת."),
)
_BY_ID = {template.template_id: template for template in TEMPLATES}


class InvalidMessage(ValueError):
    """`code` is the API's detail: unknown_template, unexpected_param or invalid_param."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def purpose_of(template_id: str | None) -> str | None:
    template = _BY_ID.get(template_id or "")
    return template.purpose if template else None


def render(template_id: str, param: str | None = None) -> str:
    template = _BY_ID.get(template_id)
    if template is None:
        raise InvalidMessage("unknown_template")
    if template.param is None:
        if param is not None:
            raise InvalidMessage("unexpected_param")
        return template.text
    if param not in template.options:
        raise InvalidMessage("invalid_param")
    return template.text.format(**{template.param: template.options[param]})


@cache
def _all_texts() -> frozenset[str]:
    return frozenset(render(t.template_id, option) for t in TEMPLATES for option in (t.options or [None]))


def is_template_text(text: str) -> bool:
    return text in _all_texts()


def as_dicts() -> list[dict]:
    return [{"template_id": t.template_id, "purpose": t.purpose, "text": t.text, "param": t.param,
             "options": dict(t.options)} for t in TEMPLATES]
