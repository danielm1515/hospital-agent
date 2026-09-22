# Sub-project 11: Required documents per appointment - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every appointment in the owner's appointment-service stores its own required document types, chosen in the booking/edit form, and `CheckAppointment` returns them as `required_documents`.

**Architecture:** A catalog table (`document_types`, seeded from `app/catalog.py` on every start) and the focused spec's link table (`appointment_required_documents`, composite primary key, foreign keys enforced by SQLite). `Appointment` exposes `required_documents` (sorted codes) through an eagerly loaded relationship, so the pydantic schema and the template read it like any other field. The form gets a checkbox group; booking and editing validate the codes against the catalog and write them in the same transaction as the appointment and its audit row.

**Tech Stack:** Python 3.12, FastAPI 0.116, SQLAlchemy 2.0 (ORM), pydantic 2.11, Jinja2, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-document-requirements-design.md` (§2 the catalog, §3 this sub-project). Read both sections first.

## Global Constraints

- The project lives outside this repo and has no git: `C:/Users/danie/Documents/ChatGPT/לימודים פרויקט גמר/appointment-service` (quote the path; it contains Hebrew). Task 1 Step 0 takes a backup; there are no commits.
- **Never** read, print, copy or modify that project's `.env`. Never restart, rebuild or stop its running container (`appointment-service-api-1`) or any other container - the controller does that after the review.
- Run its tests only this way, from that project's root, in Git Bash:
  `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src" -w /src -e PIP_ROOT_USER_ACTION=ignore -e PIP_DISABLE_PIP_VERSION_CHECK=1 python:3.12-slim sh -c "pip install -q -r requirements-dev.txt && pytest -q -p no:cacheprovider"`
  Before this sub-project it has **56** passing tests; none of them may break.
- The catalog codes, Hebrew labels and validity days are exactly the design's §2 table: `CBC` ספירת דם מלאה 90, `COAGULATION_TESTS` בדיקות קרישה 90, `ECG` תרשים פעילות חשמלית של הלב 180, `URINALYSIS` בדיקת שתן 90, `PREOP_SUMMARY` סיכום טרום ניתוח 30.
- The lookup API's contract changes only by the new `appointment.required_documents` field (sorted list of codes; `[]` when none). The API stays read-only; nothing new appears in `/docs` (booking/editing routes stay `include_in_schema=False`).
- Code and comments in English; user-facing text in Hebrew; no new dependency.

---

### Task 1: The catalog, the link table, the seed and the API field

**Files (all under the appointment-service project root):**
- Modify: `app/catalog.py` (append the document types)
- Modify: `app/models.py` (two models, one relationship, one property)
- Modify: `app/main.py` (foreign keys on SQLite; seed the catalog on every start; seed `APT-8391`'s requirements)
- Modify: `app/schemas.py` (`AppointmentOut.required_documents`)
- Test: `tests/test_required_documents.py` (create)

**Interfaces:**
- Produces: `catalog.DocumentType(code, label, max_age_days)`, `catalog.DOCUMENT_TYPES: tuple[DocumentType, ...]`, `catalog.DOCUMENT_TYPE_CODES: frozenset[str]`, `catalog.document_type_label(code) -> str`; models `DocumentTypeRow` (table `document_types`), `AppointmentRequiredDocument` (table `appointment_required_documents`), `Appointment.required_document_links` (relationship) and `Appointment.required_documents` (read-only property, sorted codes); `AppointmentOut.required_documents: list[str]`.

- [ ] **Step 0: Backup** - from the parent directory: `mkdir -p appointment-service.backup-before-sp11 && cp -r appointment-service/app appointment-service/tests appointment-service/README.md appointment-service.backup-before-sp11/` (never `.env`).

- [ ] **Step 1: Write the failing tests** - `tests/test_required_documents.py`:

```python
"""Sub-project 11: every appointment stores its own required document types (design §3)."""
import os

os.environ["ENABLE_FAILURE_SIMULATION"] = "true"
os.environ["MOCK_TIMEOUT_PATIENT_ID"] = "P-TIMEOUT"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import catalog
from app.main import create_app
from app.models import Appointment, AppointmentRequiredDocument, DocumentTypeRow
from app.patient_registry import RegistryUnavailable

CATALOG = [("CBC", "ספירת דם מלאה", 90), ("COAGULATION_TESTS", "בדיקות קרישה", 90),
           ("ECG", "תרשים פעילות חשמלית של הלב", 180), ("URINALYSIS", "בדיקת שתן", 90),
           ("PREOP_SUMMARY", "סיכום טרום ניתוח", 30)]


class FakeRegistry:
    names = {"P-10041": "דנה כהן", "P-20000": "יוסי לוי", "P-30000": "מיכל אברהם"}

    def exists(self, patient_id):
        return patient_id in self.names

    def list_patients(self):
        return sorted(self.names.items())


def make_client(tmp_path, **kwargs):
    app = create_app(f"sqlite:///{(tmp_path / 'test.db').as_posix()}", seed_demo_data=True,
                     patient_registry=FakeRegistry(), **kwargs)
    return app, TestClient(app, follow_redirects=False)


def required_of(app, appointment_id):
    with app.state.SessionLocal() as session:
        return session.get(Appointment, appointment_id).required_documents


# --- the catalog --------------------------------------------------------------------------

def test_the_catalog_is_the_designs_table():
    assert [(t.code, t.label, t.max_age_days) for t in catalog.DOCUMENT_TYPES] == CATALOG
    assert catalog.DOCUMENT_TYPE_CODES == {code for code, _, _ in CATALOG}
    assert catalog.document_type_label("ECG") == "תרשים פעילות חשמלית של הלב"
    assert catalog.document_type_label("UNKNOWN") == "UNKNOWN"


def test_the_catalog_table_is_seeded_on_every_start_even_without_demo_data(tmp_path):
    app = create_app(f"sqlite:///{(tmp_path / 'test.db').as_posix()}", seed_demo_data=False,
                     patient_registry=FakeRegistry())
    with TestClient(app):
        with app.state.SessionLocal() as session:
            rows = [(r.code, r.label_he, r.max_age_days)
                    for r in session.scalars(select(DocumentTypeRow).order_by(DocumentTypeRow.code))]
    assert rows == sorted(CATALOG)


def test_seeding_the_catalog_twice_changes_nothing(tmp_path):
    db = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    for _ in range(2):
        app = create_app(db, seed_demo_data=True, patient_registry=FakeRegistry())
        with TestClient(app):
            pass
    with app.state.SessionLocal() as session:
        assert session.scalar(text("SELECT count(*) FROM document_types")) == 5


# --- the link table -----------------------------------------------------------------------

def test_the_demo_seed_gives_apt_8391_the_focused_specs_three_types(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        assert required_of(app, "APT-8391") == ["CBC", "COAGULATION_TESTS", "ECG"]
        assert required_of(app, "APT-8392") == []


def test_an_unknown_type_is_refused_by_the_database(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        with pytest.raises(IntegrityError):
            with app.state.SessionLocal() as session:
                session.add(AppointmentRequiredDocument(appointment_id="APT-8392", document_type="ELECTRICITY_BILL"))
                session.commit()


def test_a_type_cannot_be_required_twice_for_one_appointment(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        with pytest.raises(IntegrityError):
            with app.state.SessionLocal() as session:
                session.execute(text("INSERT INTO appointment_required_documents (appointment_id, document_type) "
                                     "VALUES ('APT-8391', 'CBC')"))
                session.commit()


def test_a_requirement_for_a_missing_appointment_is_refused(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        with pytest.raises(IntegrityError):
            with app.state.SessionLocal() as session:
                session.add(AppointmentRequiredDocument(appointment_id="APT-NOPE", document_type="CBC"))
                session.commit()


# --- the API ------------------------------------------------------------------------------

def test_check_appointment_returns_the_required_documents(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        body = client.get("/api/v1/patients/P-10041/appointment").json()
        none = client.get("/api/v1/patients/P-20000/appointment").json()
    assert body["appointment"]["required_documents"] == ["CBC", "COAGULATION_TESTS", "ECG"]
    assert none["appointment"]["required_documents"] == []
    # Nothing else in the contract changed.
    assert set(body["appointment"]) == {"appointment_id", "patient_id", "department", "doctor_name",
                                        "appointment_at", "location", "status", "required_documents"}
```

- [ ] **Step 2: Run to see them fail** - expected: `ImportError` (no `AppointmentRequiredDocument` / `DocumentTypeRow`, no `catalog.DOCUMENT_TYPES`).

- [ ] **Step 3: The catalog** - append to `app/catalog.py`:

```python


@dataclass(frozen=True)
class DocumentType:
    code: str
    label: str
    max_age_days: int  # used by the document service's validity check (design §2)


# The focused spec's document types (design §2). The code is what every system stores and sends.
DOCUMENT_TYPES: tuple[DocumentType, ...] = (
    DocumentType("CBC", "ספירת דם מלאה", 90),
    DocumentType("COAGULATION_TESTS", "בדיקות קרישה", 90),
    DocumentType("ECG", "תרשים פעילות חשמלית של הלב", 180),
    DocumentType("URINALYSIS", "בדיקת שתן", 90),
    DocumentType("PREOP_SUMMARY", "סיכום טרום ניתוח", 30),
)
DOCUMENT_TYPE_CODES = frozenset(t.code for t in DOCUMENT_TYPES)
_DOCUMENT_LABELS = {t.code: t.label for t in DOCUMENT_TYPES}


def document_type_label(code: str) -> str:
    return _DOCUMENT_LABELS.get(code, code)
```

- [ ] **Step 4: The models** - in `app/models.py`: change the imports to

```python
from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
```

add, above `class Appointment`:

```python
class DocumentTypeRow(Base):
    """The document-type catalog (design §2), seeded from app/catalog.py on every start."""

    __tablename__ = "document_types"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    label_he: Mapped[str] = mapped_column(String(120), nullable=False)
    max_age_days: Mapped[int] = mapped_column(Integer, nullable=False)


class AppointmentRequiredDocument(Base):
    """The focused spec's link table: one row per (appointment, required document type)."""

    __tablename__ = "appointment_required_documents"

    appointment_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("appointments.appointment_id", ondelete="CASCADE"), primary_key=True)
    document_type: Mapped[str] = mapped_column(
        String(32), ForeignKey("document_types.code"), primary_key=True)
```

and inside `class Appointment`, after `updated_at`:

```python
    # Loaded with the appointment (selectin), so the schema and the template can read it after
    # the session closes. Replacing the list replaces the rows (delete-orphan).
    required_document_links: Mapped[list["AppointmentRequiredDocument"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin",
        order_by="AppointmentRequiredDocument.document_type")

    @property
    def required_documents(self) -> list[str]:
        return [link.document_type for link in self.required_document_links]
```

- [ ] **Step 5: Foreign keys, the catalog seed, the demo requirements** - in `app/main.py`:

Add `event` to the `from sqlalchemy import ...` line, and `DocumentTypeRow, AppointmentRequiredDocument` to the `from .models import ...` line.

Add this function after `_seed`:

```python
def _seed_document_types(session: Session) -> None:
    """The catalog is reference data, not demo data: it is written on every start, whatever
    SEED_DEMO_DATA says, and brought in line with app/catalog.py (design §2)."""
    for t in catalog.DOCUMENT_TYPES:
        row = session.get(DocumentTypeRow, t.code)
        if row is None:
            session.add(DocumentTypeRow(code=t.code, label_he=t.label, max_age_days=t.max_age_days))
        else:
            row.label_he, row.max_age_days = t.label, t.max_age_days
    session.commit()
```

In `_seed`, give the `APT-8391` appointment `required_document_links=[AppointmentRequiredDocument(document_type=c) for c in ("CBC", "COAGULATION_TESTS", "ECG")]` (the focused spec's §6.1 example; `APT-8392` gets none). Because the links point at `document_types`, the catalog must be seeded first.

In `create_app`, right after `engine = create_engine(...)`:

```python
    if effective_url.startswith("sqlite"):
        # SQLite leaves foreign keys off unless every connection asks (design §3).
        @event.listens_for(engine, "connect")
        def _foreign_keys_on(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
```

In `lifespan`, replace

```python
        Base.metadata.create_all(engine)
        if should_seed:
            with SessionLocal() as session:
                _seed(session)
```
with
```python
        Base.metadata.create_all(engine)  # creates the two new tables in an existing database too
        with SessionLocal() as session:
            _seed_document_types(session)
            if should_seed:
                _seed(session)
```

- [ ] **Step 6: The schema** - in `app/schemas.py`, add to `AppointmentOut` (after `status`): `required_documents: list[str] = []`.

- [ ] **Step 7: Run** - the new file passes, and the whole suite (56 + 8 = 64) passes.

- [ ] **Step 8: Report** - no commit (no git). List the files changed and the test count.

---

### Task 2: The form, editing, the table and the README

**Files:** `app/main.py` (booking, editing, `form_of`, `render_dashboard`), `app/templates/dashboard.html`, `README.md`; test: append to `tests/test_required_documents.py`.

**Interfaces:**
- Consumes: Task 1's `catalog.DOCUMENT_TYPES`, `catalog.DOCUMENT_TYPE_CODES`, `catalog.document_type_label`, `Appointment.required_documents`, `Appointment.required_document_links`, `AppointmentRequiredDocument`.
- Produces: form field `required_documents` (a repeated checkbox value); template context `document_types` and `document_type_label`.

- [ ] **Step 1: Write the failing tests** - append to `tests/test_required_documents.py`:

```python
# --- the form (design §3) -----------------------------------------------------------------

import re  # noqa: E402
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from app.models import AuditLog  # noqa: E402

NEXT_YEAR = datetime.now(ZoneInfo("Asia/Jerusalem")).year + 1


def csrf(client):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get("/").text).group(1)


def booking(client, **overrides):
    data = {"csrf_token": csrf(client), "patient_id": "P-30000", "department": "Orthopedics",
            "doctor_name": "Dr. Levi", "appointment_day": "14", "appointment_month": "5",
            "appointment_year": str(NEXT_YEAR), "appointment_time": "09:15", "location": "Building C, Floor 1"}
    data.update(overrides)
    return data


def only_appointment_of(app, patient_id):
    with app.state.SessionLocal() as session:
        [appointment] = session.scalars(select(Appointment).where(Appointment.patient_id == patient_id))
        return appointment


def test_the_form_offers_every_catalog_type_as_a_checkbox(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        page = client.get("/").text
    boxes = re.findall(r'<input type="checkbox" name="required_documents" value="([^"]+)"', page)
    assert boxes == [code for code, _, _ in CATALOG]
    for _, label, _ in CATALOG:
        assert label in page


def test_booking_stores_the_chosen_types(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        response = client.post("/appointments", data=booking(client, required_documents=["ECG", "CBC"]))
        appointment = only_appointment_of(app, "P-30000")
        lookup = client.get("/api/v1/patients/P-30000/appointment").json()
    assert response.status_code == 303
    assert appointment.required_documents == ["CBC", "ECG"]
    assert lookup["appointment"]["required_documents"] == ["CBC", "ECG"]


def test_booking_with_no_type_chosen_needs_nothing(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        assert client.post("/appointments", data=booking(client)).status_code == 303
        assert only_appointment_of(app, "P-30000").required_documents == []


@pytest.mark.parametrize("codes", [["ELECTRICITY_BILL"], ["CBC", "cbc"], [" "]])
def test_a_type_outside_the_catalog_books_nothing(tmp_path, codes):
    app, client = make_client(tmp_path)
    with client:
        response = client.post("/appointments", data=booking(client, required_documents=codes))
        with app.state.SessionLocal() as session:
            booked = list(session.scalars(select(Appointment).where(Appointment.patient_id == "P-30000")))
    if codes == [" "]:  # a blank value is ignored, not an error
        assert response.status_code == 303 and booked[0].required_documents == []
    else:
        assert response.status_code == 400
        assert "מהרשימה" in response.text
        assert booked == []


def test_a_repeated_type_is_stored_once(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        client.post("/appointments", data=booking(client, required_documents=["ECG", "ECG"]))
        assert only_appointment_of(app, "P-30000").required_documents == ["ECG"]


def test_the_form_keeps_the_chosen_types_after_an_error(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        response = client.post("/appointments", data=booking(client, location="Nowhere",
                                                             required_documents=["CBC", "ECG"]))
    assert response.status_code == 400
    assert 'value="CBC" checked' in response.text and 'value="ECG" checked' in response.text
    assert 'value="URINALYSIS" checked' not in response.text


def test_editing_shows_the_stored_types_not_a_derived_list(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        page = client.get("/appointments/APT-8391/edit").text
    checked = re.findall(r'value="([A-Z_]+)" checked', page)
    assert checked == ["CBC", "COAGULATION_TESTS", "ECG"]


def edit_data(client, **overrides):
    data = {"csrf_token": csrf(client), "department": "Neurology", "doctor_name": "Dr. Cohen",
            "appointment_day": "20", "appointment_month": "6", "appointment_year": str(NEXT_YEAR),
            "appointment_time": "13:45", "location": "Building B, Floor 2"}
    data.update(overrides)
    return data


def test_saving_an_edit_replaces_the_types(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        response = client.post("/appointments/APT-8391", data=edit_data(client, required_documents=["URINALYSIS"]))
        assert response.status_code == 303
        assert required_of(app, "APT-8391") == ["URINALYSIS"]
        client.post("/appointments/APT-8391", data=edit_data(client))  # none ticked
        assert required_of(app, "APT-8391") == []
        with app.state.SessionLocal() as session:
            left = session.scalar(text("SELECT count(*) FROM appointment_required_documents "
                                       "WHERE appointment_id = 'APT-8391'"))
            audit = [r.result for r in session.scalars(select(AuditLog).where(AuditLog.operation == "UpdateAppointment"))]
    assert left == 0
    assert audit == ["updated", "updated"]


def test_an_invalid_edit_leaves_the_types_as_they_were(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        response = client.post("/appointments/APT-8391",
                               data=edit_data(client, required_documents=["ELECTRICITY_BILL"]))
        assert response.status_code == 400
        assert required_of(app, "APT-8391") == ["CBC", "COAGULATION_TESTS", "ECG"]


def test_the_table_shows_each_appointments_required_documents(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        page = client.get("/").text
    assert "<th>מסמכים נדרשים</th>" in page
    assert "ספירת דם מלאה" in page and "בדיקות קרישה" in page
    header = re.search(r"<thead><tr>(.*?)</tr></thead>", page, re.S).group(1)
    for row in re.findall(r"<tr>(.*?)</tr>", re.search(r"<tbody>(.*?)</tbody>", page, re.S).group(1), re.S):
        assert row.count("<td") == header.count("<th")
```

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Validation** - in `app/main.py`, after `_validate_booking`, add:

```python
def _validate_required_documents(codes: list[str]) -> tuple[str | None, list[str]]:
    """(error, the codes to store): blanks dropped, repeats merged, sorted; a code outside the
    catalog refuses the whole form - the database would refuse it anyway (design §3)."""
    cleaned = sorted({code.strip() for code in codes if code.strip()})
    if any(code not in catalog.DOCUMENT_TYPE_CODES for code in cleaned):
        return "יש לבחור מסמכים נדרשים מהרשימה בלבד.", []
    return None, cleaned
```

- [ ] **Step 4: Booking** - `book_appointment` gains the parameter `required_documents: list[str] = Form(default=[])`. After `error, when = _validate_booking(form)` and its early return, add:

```python
        doc_error, codes = _validate_required_documents(required_documents)
        form["required_documents"] = [c for c in required_documents if c in catalog.DOCUMENT_TYPE_CODES]
        if doc_error:
            return render_dashboard(request, error=doc_error, form=form, status_code=400)
```

Also set `form["required_documents"] = [c for c in required_documents if c in catalog.DOCUMENT_TYPE_CODES]` **before** the `_validate_booking` error return, so a booking error keeps the ticks (move the line up; keep a single assignment). In `session.add(Appointment(...))` add `required_document_links=[AppointmentRequiredDocument(document_type=c) for c in codes]`.

- [ ] **Step 5: Editing** - `update_appointment` gains the same parameter. Build `form["required_documents"]` the same way right after building `form`; validate with `_validate_required_documents` after `_validate_booking` (both errors re-render with `editing=appointment`, status 400, and change nothing). On success, add `appointment.required_document_links = [AppointmentRequiredDocument(document_type=c) for c in codes]` next to the other assignments (before the audit call, so it commits with them). `form_of` gains `"required_documents": appointment.required_documents`.

- [ ] **Step 6: The template context** - in `render_dashboard`'s context add `"document_types": catalog.DOCUMENT_TYPES,` and `"document_type_label": catalog.document_type_label,`.

- [ ] **Step 7: The template** - in `app/templates/dashboard.html`:

  - CSS (add to the `<style>` block, next to `.booking-warning{`): `.docs-field{grid-column:1/-1}.doc-options{display:flex;flex-wrap:wrap;gap:8px 20px}.doc-option{display:inline-flex;align-items:center;gap:7px;font-size:14px;color:var(--ink-700);cursor:pointer}.doc-option .id{font-size:12px;color:var(--ink-500)}.doc-list{white-space:normal;min-width:160px;font-size:13px}`
  - Before `<div class="booking-actions">`, insert the field:

```html
        <div class="field docs-field"><span class="field-label" id="book_docs_label">מסמכים נדרשים לתור <span class="optional">(לא חובה)</span></span><div class="doc-options" role="group" aria-labelledby="book_docs_label">{% for t in document_types %}<label class="doc-option"><input type="checkbox" name="required_documents" value="{{ t.code }}"{% if t.code in (form.required_documents or []) %} checked{% endif %}>{{ t.label }} <span class="id">{{ t.code }}</span></label>{% endfor %}</div></div>
```

    (Keep the exact `<input type="checkbox" name="required_documents" value="…"` attribute order and `value="…" checked` - the tests match it.)
  - In the table header, after `<th>מיקום</th>` add `<th>מסמכים נדרשים</th>`; in each row, after the location cell (`{{ appointment.location or "לא צוין" }}</td>`) add `<td class="doc-list">{% if appointment.required_documents %}{% for code in appointment.required_documents %}{{ document_type_label(code) }}{% if not loop.last %}, {% endif %}{% endfor %}{% else %}ללא{% endif %}</td>`.
  - Raise the table's `min-width` from `1020px` to `1180px`.

- [ ] **Step 8: README** - in the section "קביעת וביטול תורים בממשק", add a paragraph (Hebrew): לכל תור אפשר לסמן את המסמכים הנדרשים לו מתוך קטלוג קבוע של חמישה סוגים (`CBC`, `COAGULATION_TESTS`, `ECG`, `URINALYSIS`, `PREOP_SUMMARY`). הבחירה נשמרת עם התור בטבלה `appointment_required_documents`, והיא לא נגזרת מהרופא או מהמחלקה. בעריכה מוצגת הבחירה השמורה, ושמירה מחליפה אותה. `CheckAppointment` מחזיר אותה בשדה `required_documents`, רשימה ממוינת, וריקה כשאין דרישות. And in "בדיקת תור קיים", note that the response now includes `required_documents`.

- [ ] **Step 9: Run** - the whole suite passes (56 + 8 + 12 = 76). Report files changed and the count; no commit.
