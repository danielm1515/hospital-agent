"""The fixed staff messages (sub-project 15, design §7.1): no free text, parameters from closed lists."""
import pytest

from hospital_agent import patient_messages as pm
from hospital_agent.documents import CATALOG_LABELS


def test_a_plain_template_renders_its_text():
    assert pm.render("clarify_general") == "לא הצלחנו להבין את פנייתך. נשמח אם תפרט/י במה נוכל לעזור."


def test_did_you_mean_takes_a_topic_from_its_closed_list():
    assert pm.render("clarify_did_you_mean", "appointment_time") == "האם התכוונת למועד התור? נשמח לאישור או לפירוט."


def test_a_document_request_names_the_catalog_label():
    assert pm.render(pm.DOCUMENT_REQUEST, "URINALYSIS") == "נא להעלות את המסמך: בדיקת שתן."


@pytest.mark.parametrize("template_id, param, code", [
    ("no_such_template", None, "unknown_template"),
    ("clarify_general", "appointment_time", "unexpected_param"),
    ("clarify_did_you_mean", None, "invalid_param"),
    ("clarify_did_you_mean", "anything goes", "invalid_param"),
    (pm.DOCUMENT_REQUEST, "X-RAY", "invalid_param"),
])
def test_anything_outside_the_lists_is_refused(template_id, param, code):
    with pytest.raises(pm.InvalidMessage) as invalid:
        pm.render(template_id, param)
    assert invalid.value.code == code


def test_every_rendered_text_is_recognised_and_nothing_else_is():
    assert pm.is_template_text(pm.render("close_no_reply"))
    for code in CATALOG_LABELS:
        assert pm.is_template_text(pm.render(pm.DOCUMENT_REQUEST, code))
    assert not pm.is_template_text("לא הצלחנו להבין את פנייתך.")  # a fragment is not a template
    assert not pm.is_template_text("קח שני כדורים ביום")


def test_purposes_and_the_api_shape():
    assert {pm.purpose_of(t.template_id) for t in pm.TEMPLATES} == {"question", "document", "closing"}
    assert pm.purpose_of("nope") is None
    shapes = pm.as_dicts()
    assert set(shapes[0]) == {"template_id", "purpose", "text", "param", "options"}
    document = next(s for s in shapes if s["template_id"] == pm.DOCUMENT_REQUEST)
    assert document["param"] == "document" and document["options"] == CATALOG_LABELS
