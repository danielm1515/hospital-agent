# Sub-project 12: The document service - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new, standalone `document-service` that takes a patient's PDF, checks it (readable, medical, catalog type, the patient's own, not a duplicate, within its validity), classifies it with an LLM, stores only accepted files in a private S3 bucket, and lists a patient's documents for the Hospital Agent's `CheckDocuments`.

**Architecture:** FastAPI + SQLAlchemy + SQLite for metadata (like `appointment-service`), one container on `http://localhost:8090`. The intake is a pure function (`app/intake.py`) over two ports - a `Classifier` (OpenAI, or a deterministic fake) and an `ObjectStore` (S3, or in memory) - so the whole pipeline is tested with no network. Every call needs `X-API-Key`; errors are JSON codes; audit rows carry codes and ids only.

**Tech Stack:** Python 3.12, FastAPI 0.116.1, SQLAlchemy 2.0.43, pydantic 2.11.7, uvicorn 0.35.0, python-multipart 0.0.20, pypdf 6.19.0, boto3 1.43.99, openai 3.17.0; pytest 8.4.1, httpx 0.28.1. Demo PDFs rendered by the host's Chrome (headless).

**Spec:** `docs/superpowers/specs/2026-09-22-document-requirements-design.md` §2 (catalog) and §4 (this sub-project). Read both first.

## Global Constraints

- The project is new: `C:/Users/danie/Documents/ChatGPT/לימודים פרויקט גמר/document-service` (quote the path - it contains Hebrew). Task 1 creates it and runs `git init` there (local only - never a remote, never a push). Commit after each task; every commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Secrets:** never read, print or copy any `.env` (this project's, the Hospital Agent's or the appointment-service's). `.env` is git-ignored from the first commit. The OpenAI key and the AWS keys are put there by the owner.
- **Never** touch the owner's running containers (`appointment-service-api-1`, `hospital-agent-*`, and others). Run this project's tests only this way, from its root, in Git Bash:
  `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src" -w /src -e PIP_ROOT_USER_ACTION=ignore -e PIP_DISABLE_PIP_VERSION_CHECK=1 python:3.12-slim sh -c "pip install -q -r requirements-dev.txt && pytest -q -p no:cacheprovider"`
- The catalog is exactly the design's §2: `CBC` ספירת דם מלאה 90, `COAGULATION_TESTS` בדיקות קרישה 90, `ECG` תרשים פעילות חשמלית של הלב 180, `URINALYSIS` בדיקת שתן 90, `PREOP_SUMMARY` סיכום טרום ניתוח 30.
- Result codes are exactly: `ACCEPTED`, `NON_MEDICAL_DOCUMENT`, `DOCUMENT_UNREADABLE`, `DOCUMENT_EXPIRED`, `DUPLICATE_DOCUMENT`, `PATIENT_MISMATCH`.
- Fail closed everywhere: no confident answer -> `DOCUMENT_UNREADABLE`; no date -> `DOCUMENT_EXPIRED`; storage or classifier not configured or failing -> `503`, nothing stored.
- Only an `ACCEPTED` file is written to S3; no file name, text or content is ever logged or audited; the application log never holds `patient_id`.
- Code and comments in English; README in Hebrew like `appointment-service`'s; the prompt in English.

---

### Task 1: The project skeleton, the catalog, the models, health and auth

**Files (project root `document-service/`):**
- Create: `.gitignore`, `.env.example`, `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `Dockerfile`, `compose.yaml`
- Create: `app/__init__.py`, `app/config.py`, `app/catalog.py`, `app/models.py`, `app/main.py`
- Test: `tests/__init__.py` (empty), `tests/conftest.py`, `tests/test_service.py`

**Interfaces:**
- Produces: `catalog.DocumentType(code, label, max_age_days)`, `catalog.DOCUMENT_TYPES`, `catalog.BY_CODE`, `catalog.CODES`; `models.Base`, `models.Document`, `models.AuditLog`; `main.create_app(database_url=None, *, api_key=None, api_auth_enabled=None, classifier=FROM_ENV, store=FROM_ENV, today=None) -> FastAPI`, `main.FROM_ENV`, `main.PATIENT_ID_PATTERN`; `app.state.SessionLocal`, `app.state.classifier`, `app.state.store`, `app.state.today` (a `Callable[[], date]`).

- [ ] **Step 1: Create the project and its files.**

`mkdir` the project directory, then `git init` in it.

`.gitignore`:
```
.env
__pycache__/
.pytest_cache/
*.db
```

`.env.example`:
```
# Copy to .env and fill in. Never commit .env.
DOCUMENT_API_KEY=replace-with-a-random-64-character-secret
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.6-luna
S3_BUCKET=
AWS_REGION=eu-north-1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
```

`requirements.txt`:
```
fastapi==0.116.1
uvicorn[standard]==0.35.0
SQLAlchemy==2.0.43
pydantic==2.11.7
python-multipart==0.0.20
pypdf==6.19.0
boto3==1.43.99
openai==3.17.0
```

`requirements-dev.txt`:
```
-r requirements.txt
httpx==0.28.1
pytest==8.4.1
```

`pytest.ini`:
```
[pytest]
pythonpath = .
testpaths = tests
```

`Dockerfile`:
```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p /data \
    && chown app:app /data

COPY app ./app

VOLUME ["/data"]

USER app
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`compose.yaml`:
```yaml
services:
  api:
    build: .
    environment:
      DATABASE_URL: sqlite:////data/documents.db
      API_AUTH_ENABLED: "true"
      DOCUMENT_API_KEY: ${DOCUMENT_API_KEY:-}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
      OPENAI_MODEL: ${OPENAI_MODEL:-gpt-5.6-luna}
      S3_BUCKET: ${S3_BUCKET:-}
      AWS_REGION: ${AWS_REGION:-eu-north-1}
      AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID:-}
      AWS_SECRET_ACCESS_KEY: ${AWS_SECRET_ACCESS_KEY:-}
    ports:
      - "127.0.0.1:8090:8000"
    volumes:
      - document_data:/data
    restart: unless-stopped

volumes:
  document_data:
```

`app/__init__.py`: empty.

`app/config.py`:
```python
import os
from dataclasses import dataclass, field


def _as_bool(value: str | None, default: bool) -> bool:
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    """Read when the app is built, never at import time, so tests control the environment."""

    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./documents.db"))
    api_auth_enabled: bool = field(default_factory=lambda: _as_bool(os.getenv("API_AUTH_ENABLED"), True))
    api_key: str = field(default_factory=lambda: _env("DOCUMENT_API_KEY"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-5.6-luna"))
    s3_bucket: str = field(default_factory=lambda: _env("S3_BUCKET"))
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", "eu-north-1"))
    max_upload_bytes: int = 10 * 1024 * 1024
```

`app/catalog.py`:
```python
"""The document types this service recognises (HospitalAgent design §2). The code is what every
system stores and sends; max_age_days is the validity rule the intake applies."""
from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentType:
    code: str
    label: str
    max_age_days: int


DOCUMENT_TYPES: tuple[DocumentType, ...] = (
    DocumentType("CBC", "ספירת דם מלאה", 90),
    DocumentType("COAGULATION_TESTS", "בדיקות קרישה", 90),
    DocumentType("ECG", "תרשים פעילות חשמלית של הלב", 180),
    DocumentType("URINALYSIS", "בדיקת שתן", 90),
    DocumentType("PREOP_SUMMARY", "סיכום טרום ניתוח", 30),
)
BY_CODE = {t.code: t for t in DOCUMENT_TYPES}
CODES = tuple(t.code for t in DOCUMENT_TYPES)
```

`app/models.py`:
```python
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Document(Base):
    """One upload, accepted or not. Only an accepted one has an s3_object_key."""

    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    patient_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(32))
    document_date: Mapped[date | None] = mapped_column(Date)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    s3_object_key: Mapped[str | None] = mapped_column(String(200))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditLog(Base):
    """Codes and ids only - never a file name, text or content."""

    __tablename__ = "document_audit_logs"

    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    patient_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
```

`app/main.py` (Task 1's part; Tasks 3-4 add the intake wiring and the two document routes):
```python
import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import AuditLog, Base

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("document-service")

CLINIC_TZ = ZoneInfo("Asia/Jerusalem")
PATIENT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
FROM_ENV = object()  # "build this port from the environment" (the default for classifier/store)


def _israel_today() -> date:
    return datetime.now(CLINIC_TZ).date()


def write_audit(session: Session, *, patient_id: str, operation: str, result: str,
                document_id: str | None, latency_ms: int) -> None:
    """Adds the audit row and commits it with whatever else is pending. The log line carries no
    patient_id (it is in the audit table, which is access-controlled)."""
    logger.info(json.dumps({"operation": operation, "result": result, "document_id": document_id,
                            "latency_ms": latency_ms}))
    session.add(AuditLog(audit_id=str(uuid4()), patient_id=patient_id, operation=operation, result=result,
                         document_id=document_id, latency_ms=latency_ms))
    session.commit()


def create_app(database_url: str | None = None, *, api_key: str | None = None,
               api_auth_enabled: bool | None = None, classifier=FROM_ENV, store=FROM_ENV,
               today: Callable[[], date] | None = None) -> FastAPI:
    settings = Settings()
    url = database_url or settings.database_url
    auth_on = settings.api_auth_enabled if api_auth_enabled is None else api_auth_enabled
    key = settings.api_key if api_key is None else api_key
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="Document Service", version="1.0.0", lifespan=lifespan,
                  description="Patients' medical documents: intake, classification and listing.")
    app.state.settings = settings
    app.state.SessionLocal = SessionLocal
    app.state.today = today or _israel_today
    app.state.classifier = None if classifier is FROM_ENV else classifier  # Task 3 builds it from env
    app.state.store = None if store is FROM_ENV else store  # Task 4 builds it from env

    def authorised(request: Request) -> bool:
        if not auth_on:
            return True
        supplied = request.headers.get("X-API-Key", "")
        return bool(key) and bool(supplied) and secrets.compare_digest(supplied, key)

    app.state.authorised = authorised

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "validation_error"})

    @app.get("/health")
    def health(request: Request) -> JSONResponse:
        try:
            with request.app.state.SessionLocal() as session:
                session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"status": "degraded", "database": "unavailable"})
        return JSONResponse({
            "status": "ok", "database": "ok",
            "classifier": "configured" if request.app.state.classifier is not None else "not_configured",
            "storage": "configured" if request.app.state.store is not None else "not_configured",
        })

    return app


app = create_app()
```

`tests/conftest.py`:
```python
"""The tests never reach OpenAI or AWS: a shell that has the real variables set must not turn a
test into a live call, so they are cleared before every test. A test that wants a port passes it."""
import pytest

LIVE_VARIABLES = ("OPENAI_API_KEY", "S3_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                  "AWS_SESSION_TOKEN", "AWS_PROFILE", "DOCUMENT_API_KEY")


@pytest.fixture(autouse=True)
def no_live_configuration(monkeypatch):
    for name in LIVE_VARIABLES:
        monkeypatch.delenv(name, raising=False)
```

- [ ] **Step 2: Write the failing tests** - `tests/test_service.py`:

```python
from fastapi.testclient import TestClient

from app import catalog
from app.main import create_app


def make(tmp_path, **kwargs):
    return create_app(f"sqlite:///{(tmp_path / 'd.db').as_posix()}", **kwargs)


def test_the_catalog_is_the_designs_table():
    assert [(t.code, t.label, t.max_age_days) for t in catalog.DOCUMENT_TYPES] == [
        ("CBC", "ספירת דם מלאה", 90), ("COAGULATION_TESTS", "בדיקות קרישה", 90),
        ("ECG", "תרשים פעילות חשמלית של הלב", 180), ("URINALYSIS", "בדיקת שתן", 90),
        ("PREOP_SUMMARY", "סיכום טרום ניתוח", 30)]


def test_health_says_what_is_configured(tmp_path):
    with TestClient(make(tmp_path, classifier=None, store=None)) as client:
        body = client.get("/health").json()
    assert body == {"status": "ok", "database": "ok", "classifier": "not_configured", "storage": "not_configured"}


def test_health_needs_no_key(tmp_path):
    with TestClient(make(tmp_path, api_key="k")) as client:
        assert client.get("/health").status_code == 200


def test_without_a_configured_key_nothing_is_authorised(tmp_path):
    app = make(tmp_path, api_key="")
    with TestClient(app):
        class R:  # the minimal request shape authorised() reads
            headers = {"X-API-Key": ""}
        assert app.state.authorised(R()) is False
        R.headers = {"X-API-Key": "anything"}
        assert app.state.authorised(R()) is False


def test_the_right_key_is_authorised_and_a_wrong_one_is_not(tmp_path):
    app = make(tmp_path, api_key="secret-key")
    with TestClient(app):
        class R:
            headers = {"X-API-Key": "secret-key"}
        assert app.state.authorised(R()) is True
        R.headers = {"X-API-Key": "nope"}
        assert app.state.authorised(R()) is False
```

- [ ] **Step 3: Run** - all 5 pass (the files above are the implementation; there is no separate red step for scaffolding, say so in the report).

- [ ] **Step 4: Commit** - `git add -A` (check `git status` first: no `.env`, no `*.db`) and commit `Create the document service: skeleton, catalog, models, health and auth`.

---

### Task 2: The demo PDFs (2026 copies) and the test fixtures

**Files:**
- Create: `demo/make_demo_pdfs.py`, `demo/README.md`
- Create (generated, committed): `demo/2026/01_…pdf` … `06_…pdf`, `tests/fixtures/2026/` (same six), `tests/fixtures/2024/` (the owner's six originals, copied)
- Test: `tests/test_fixtures.py`

**Interfaces:** Produces the fixture files, named `cbc.pdf`, `coagulation.pdf`, `ecg.pdf`, `urinalysis.pdf`, `preop_summary.pdf`, `electricity_bill.pdf` in both `tests/fixtures/2024/` and `tests/fixtures/2026/`. The 2026 set has issue date **15.09.2026** (fixed, so tests are deterministic).

- [ ] **Step 1: The originals** - copy the owner's six files from `C:/Users/danie/Downloads/` into `tests/fixtures/2024/` under the English names: `01_ספירת_דם_מלאה.pdf`->`cbc.pdf`, `02_בדיקות_קרישה.pdf`->`coagulation.pdf`, `03_תרשים_לב.pdf`->`ecg.pdf`, `04_בדיקת_שתן_לא_רלוונטית.pdf`->`urinalysis.pdf`, `05_סיכום_טרום_ניתוח.pdf`->`preop_summary.pdf`, `06_מסמך_לא_רפואי_חשבון_חשמל.pdf`->`electricity_bill.pdf`. Read only; never modify the originals.

- [ ] **Step 2: The generator** - `demo/make_demo_pdfs.py` (runs on the Windows host with its own Python, standard library only; renders HTML with the installed Chrome):

```python
"""Renders the six demo documents as PDFs dated ISSUE_DATE, with the host's Chrome (headless).

The owner's originals are dated 2024 and so are expired under every validity rule; these copies
carry the same content with a current date. Usage (Windows host):
    python demo/make_demo_pdfs.py            # issue date 15.09.2026 -> demo/2026 and tests/fixtures/2026
    python demo/make_demo_pdfs.py 01.12.2026 demo/december
"""
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
ROOT = Path(__file__).resolve().parent.parent

STYLE = """body{font-family:Arial,sans-serif;margin:48px;color:#1b2430}h1{font-size:22px;margin:0}
.org{color:#555;margin-bottom:18px}h2{font-size:19px;margin:18px 0 8px}table{border-collapse:collapse;width:100%}
td,th{border:1px solid #ccc;padding:6px 10px;text-align:right}.meta td{border:none;padding:2px 0}"""

HEADER = """<h1>המרכז הרפואי רמון</h1><div class="org">מחלקת מסמכים רפואיים</div>
<table class="meta"><tr><td>תאריך הפקה: {date}</td></tr><tr><td>מידע המטופל: מסווג</td></tr>
<tr><td>מקום ביצוע: המרכז הרפואי רמון</td></tr></table>"""

DOCUMENTS = {
    "cbc": ("01_ספירת_דם_מלאה", "מסמך רפואי", """<h2>תוצאות מעבדה - ספירת דם מלאה</h2>
<table><tr><th>בדיקה</th><th>תוצאה</th><th>טווח ייחוס</th><th>יחידות</th></tr>
<tr><td>תאי דם לבנים</td><td>6.8</td><td>4.0 - 10.0</td><td>אלפים למיקרוליטר</td></tr>
<tr><td>המוגלובין</td><td>14.2</td><td>13.5 - 17.5</td><td>גרם לדציליטר</td></tr>
<tr><td>טסיות דם</td><td>245</td><td>150 - 400</td><td>אלפים למיקרוליטר</td></tr></table>"""),
    "coagulation": ("02_בדיקות_קרישה", "מסמך רפואי", """<h2>תוצאות מעבדה - בדיקות קרישה</h2>
<table><tr><th>בדיקה</th><th>תוצאה</th><th>טווח ייחוס</th><th>יחידות</th></tr>
<tr><td>זמן פרותרומבין</td><td>12.4</td><td>11.0 - 14.0</td><td>שניות</td></tr>
<tr><td>יחס מנורמל בינלאומי</td><td>1.02</td><td>0.90 - 1.20</td><td>יחס</td></tr></table>"""),
    "ecg": ("03_תרשים_לב", "מסמך רפואי", """<h2>תרשים פעילות חשמלית של הלב</h2>
<table><tr><td>סוג הבדיקה</td><td>תרשים פעילות חשמלית של הלב במנוחה</td></tr>
<tr><td>קצב לב</td><td>72 פעימות בדקה</td></tr><tr><td>קצב</td><td>סדיר</td></tr></table>"""),
    "urinalysis": ("04_בדיקת_שתן_לא_רלוונטית", "מסמך רפואי", """<h2>תוצאות מעבדה - בדיקת שתן כללית</h2>
<table><tr><th>מדד</th><th>תוצאה</th><th>טווח ייחוס</th></tr>
<tr><td>משקל סגולי</td><td>1.018</td><td>1.005 - 1.030</td></tr>
<tr><td>חלבון</td><td>שלילי</td><td>שלילי</td></tr></table>"""),
    "preop_summary": ("05_סיכום_טרום_ניתוח", "מסמך סיכום רפואי", """<h2>סיכום הערכה רפואית טרום-ניתוחית</h2>
<table><tr><td>מטרת הביקור</td><td>הערכת מוכנות לקראת הליך ניתוחי מתוכנן</td></tr>
<tr><td>מסמכים שנבדקו</td><td>ספירת דם מלאה, בדיקות קרישה, תרשים פעילות חשמלית של הלב</td></tr></table>"""),
    "electricity_bill": ("06_מסמך_לא_רפואי_חשבון_חשמל", "מסמך שאינו רפואי", """<h2>חשבון חשמל</h2>
<table><tr><td>חברת החשמל האזורית</td><td>חשבון צריכת חשמל תקופתי</td></tr>
<tr><td>סוג המסמך</td><td>חשבון שירות - לא מסמך רפואי</td></tr>
<tr><td>סכום לתשלום</td><td>384.70 ₪</td></tr></table>"""),
}


def render(issue_date: str, out_dirs: list[Path]) -> None:
    for d in out_dirs:
        d.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, (hebrew_name, kind, body) in DOCUMENTS.items():
            header = HEADER.format(date=issue_date) if name != "electricity_bill" else \
                f"<h1>חשבון חשמל</h1><table class='meta'><tr><td>תאריך הפקה: {issue_date}</td></tr></table>"
            html = (f"<!doctype html><html lang='he' dir='rtl'><head><meta charset='utf-8'><style>{STYLE}</style>"
                    f"</head><body>{header}<p>{kind}</p>{body}<p>מסמך ממוחשב - מסמך דוגמה</p></body></html>")
            source = Path(tmp) / f"{name}.html"
            source.write_text(html, encoding="utf-8")
            target = Path(tmp) / f"{name}.pdf"
            subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            f"--print-to-pdf={target}", source.as_uri()], check=True, capture_output=True)
            for d in out_dirs:
                file_name = f"{hebrew_name}.pdf" if d.name != "2026" or d.parent.name != "fixtures" else f"{name}.pdf"
                (d / file_name).write_bytes(target.read_bytes())


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else "15.09.2026"
    outs = [Path(sys.argv[2])] if len(sys.argv) > 2 else [ROOT / "demo" / "2026", ROOT / "tests" / "fixtures" / "2026"]
    render(date_arg, outs)
    print(f"rendered {len(DOCUMENTS)} documents dated {date_arg} into", ", ".join(str(o) for o in outs))
```

Run it once on the host: `python demo/make_demo_pdfs.py` (from the project root). `demo/2026/` gets the Hebrew file names (for the owner to upload); `tests/fixtures/2026/` gets the English names.

`demo/README.md` (Hebrew, short): what the six files are, that `demo/2026` is for uploading in the demo and the 2024 originals show `DOCUMENT_EXPIRED`, the expected result of each (the design's table: CBC/COAGULATION_TESTS/ECG accepted; URINALYSIS accepted but not required by the example appointment; PREOP_SUMMARY its own type; the electricity bill `NON_MEDICAL_DOCUMENT`), and how to regenerate with another date.

- [ ] **Step 3: The fixture tests** - `tests/test_fixtures.py`:

```python
"""The fixtures the intake tests use: both sets present, readable, and dated as expected."""
import re
from pathlib import Path

import pypdf
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
NAMES = ("cbc", "coagulation", "ecg", "urinalysis", "preop_summary", "electricity_bill")


def text_of(path: Path) -> str:
    return "".join(page.extract_text() or "" for page in pypdf.PdfReader(path).pages)


@pytest.mark.parametrize("year", ["2024", "2026"])
@pytest.mark.parametrize("name", NAMES)
def test_every_fixture_is_a_readable_pdf_with_its_year(year, name):
    path = FIXTURES / year / f"{name}.pdf"
    assert path.read_bytes().startswith(b"%PDF-")
    text = text_of(path)
    assert text.strip()
    first_date = re.search(r"\d{2}\.\d{2}\.(\d{4})", text)
    assert first_date and first_date.group(1) == year


def test_the_2026_set_is_issued_on_15_september():
    assert "15.09.2026" in text_of(FIXTURES / "2026" / "cbc.pdf")
```

- [ ] **Step 4: Run** - 13 new tests pass (18 in all).

- [ ] **Step 5: Commit** - `Add the demo documents: 2026 copies, the 2024 originals as fixtures, and their generator`.

---

### Task 3: The classifier and the intake pipeline

**Files:**
- Create: `app/classifier.py`, `app/intake.py`
- Modify: `app/main.py` (build the classifier from the environment)
- Test: `tests/test_classifier.py`, `tests/test_intake.py`

**Interfaces:**
- Produces: `classifier.Classification(is_medical: bool, document_type: str | None, document_date: date | None, patient_identifier: str | None)`; `classifier.ClassifierFailed(Exception)`; `classifier.Classifier` (Protocol: `classify(text: str) -> Classification`); `classifier.FakeClassifier`; `classifier.OpenAIClassifier(api_key: str, model: str, *, client=None, timeout_seconds: float = 30.0)`; `classifier.SCHEMA`, `classifier.PROMPT`, `classifier.MAX_TEXT_CHARS = 8000`; `intake.IntakeOutcome(result, document_type, document_date, sha256, size_bytes)`; `intake.run_intake(data: bytes, patient_id: str, *, classifier: Classifier, today: date, is_duplicate: Callable[[str], bool], max_bytes: int) -> IntakeOutcome`; the result constants `ACCEPTED`, `NON_MEDICAL_DOCUMENT`, `DOCUMENT_UNREADABLE`, `DOCUMENT_EXPIRED`, `DUPLICATE_DOCUMENT`, `PATIENT_MISMATCH` in `intake`.

- [ ] **Step 1: Write the failing tests.**

`tests/test_classifier.py`:
```python
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pypdf
import pytest

from app.classifier import (MAX_TEXT_CHARS, PROMPT, SCHEMA, Classification, ClassifierFailed, FakeClassifier,
                            OpenAIClassifier)

FIXTURES = Path(__file__).parent / "fixtures"


def text_of(year, name):
    return "".join(p.extract_text() or "" for p in pypdf.PdfReader(FIXTURES / year / f"{name}.pdf").pages)


@pytest.mark.parametrize("name, expected", [
    ("cbc", ("CBC", True)), ("coagulation", ("COAGULATION_TESTS", True)), ("ecg", ("ECG", True)),
    ("urinalysis", ("URINALYSIS", True)), ("preop_summary", ("PREOP_SUMMARY", True)),
    ("electricity_bill", (None, False)),
])
@pytest.mark.parametrize("year", ["2024", "2026"])
def test_the_fake_classifies_every_demo_file(year, name, expected):
    got = FakeClassifier().classify(text_of(year, name))
    assert (got.document_type, got.is_medical) == expected
    assert got.document_date == (date(2024, 8, 12) if year == "2024" else date(2026, 9, 15))
    assert got.patient_identifier is None


def test_the_preop_summary_is_not_mistaken_for_the_tests_it_lists():
    """It names CBC, coagulation and ECG among the documents it checked - still PREOP_SUMMARY."""
    assert FakeClassifier().classify(text_of("2026", "preop_summary")).document_type == "PREOP_SUMMARY"


def test_the_fake_reads_a_patient_identifier_of_the_idp_shape():
    got = FakeClassifier().classify("ספירת דם מלאה מסמך רפואי 01.09.2026 מטופל P-20000")
    assert got.patient_identifier == "P-20000"


def test_the_fake_fails_on_empty_text():
    with pytest.raises(ClassifierFailed):
        FakeClassifier().classify("   ")


class FakeCompletions:
    def __init__(self, content=None, raises=None):
        self.content, self.raises, self.calls = content, raises, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def openai_with(content=None, raises=None):
    completions = FakeCompletions(content, raises)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return OpenAIClassifier("k", "gpt-5.6-luna", client=client), completions


def answer(**overrides):
    base = {"is_medical": True, "document_type": "CBC", "document_date": "2026-09-15", "patient_identifier": None}
    return json.dumps({**base, **overrides})


def test_openai_call_shape():
    classifier, completions = openai_with(answer())
    result = classifier.classify("x" * (MAX_TEXT_CHARS + 500))
    assert result == Classification(True, "CBC", date(2026, 9, 15), None)
    [call] = completions.calls
    assert call["model"] == "gpt-5.6-luna" and call["reasoning_effort"] == "none"
    assert call["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "document_classification", "strict": True, "schema": SCHEMA}}
    assert call["messages"][0] == {"role": "system", "content": PROMPT}
    assert len(call["messages"][1]["content"]) == MAX_TEXT_CHARS  # only the capped extracted text is sent


@pytest.mark.parametrize("content", [
    "not json", "[]", answer(document_type="ELECTRICITY"), answer(is_medical="yes"),
    answer(document_date="15/09/2026"), json.dumps({"is_medical": True}),
])
def test_an_unusable_answer_fails(content):
    classifier, _ = openai_with(content)
    with pytest.raises(ClassifierFailed):
        classifier.classify("text")


def test_an_api_error_fails():
    import openai
    classifier, _ = openai_with(raises=openai.APIConnectionError(request=None))
    with pytest.raises(ClassifierFailed):
        classifier.classify("text")


def test_a_null_date_and_type_are_allowed_answers():
    classifier, _ = openai_with(answer(is_medical=False, document_type=None, document_date=None))
    assert classifier.classify("text") == Classification(False, None, None, None)


def test_the_prompt_asks_for_classification_only():
    assert "never interpret" in PROMPT.lower()
```

`tests/test_intake.py`:
```python
from datetime import date
from pathlib import Path

import pytest

from app.classifier import Classification, ClassifierFailed, FakeClassifier
from app.intake import (ACCEPTED, DOCUMENT_EXPIRED, DOCUMENT_UNREADABLE, DUPLICATE_DOCUMENT, NON_MEDICAL_DOCUMENT,
                        PATIENT_MISMATCH, run_intake)

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 22)
MAX = 10 * 1024 * 1024


def data(year, name):
    return (FIXTURES / year / f"{name}.pdf").read_bytes()


def intake(raw, *, classifier=None, today=TODAY, duplicates=(), patient="P-10041"):
    return run_intake(raw, patient, classifier=classifier or FakeClassifier(), today=today,
                      is_duplicate=lambda sha: sha in duplicates, max_bytes=MAX)


@pytest.mark.parametrize("name, doc_type", [("cbc", "CBC"), ("coagulation", "COAGULATION_TESTS"), ("ecg", "ECG"),
                                            ("urinalysis", "URINALYSIS"), ("preop_summary", "PREOP_SUMMARY")])
def test_the_2026_medical_files_are_accepted_with_their_type(name, doc_type):
    outcome = intake(data("2026", name))
    assert (outcome.result, outcome.document_type, outcome.document_date) == (ACCEPTED, doc_type, date(2026, 9, 15))
    assert len(outcome.sha256) == 64 and outcome.size_bytes == len(data("2026", name))


@pytest.mark.parametrize("name", ["cbc", "coagulation", "ecg", "urinalysis", "preop_summary"])
def test_the_2024_originals_are_expired(name):
    assert intake(data("2024", name)).result == DOCUMENT_EXPIRED


@pytest.mark.parametrize("year", ["2024", "2026"])
def test_the_electricity_bill_is_not_medical(year):
    assert intake(data(year, "electricity_bill")).result == NON_MEDICAL_DOCUMENT


@pytest.mark.parametrize("raw", [b"", b"hello", b"%PDF-1.7 but not really a pdf", b"GIF89a"])
def test_something_that_is_not_a_readable_pdf_is_unreadable(raw):
    assert intake(raw).result == DOCUMENT_UNREADABLE


def test_a_file_over_the_size_limit_is_unreadable_without_being_parsed():
    class Boom:
        def classify(self, text):
            raise AssertionError("must not be called")
    assert intake(b"%PDF-" + b"0" * MAX, classifier=Boom()).result == DOCUMENT_UNREADABLE


def test_a_pdf_with_no_text_is_unreadable():
    import io
    import pypdf
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    assert intake(buffer.getvalue()).result == DOCUMENT_UNREADABLE


def test_an_accepted_duplicate_is_refused_before_classification():
    raw = data("2026", "cbc")
    first = intake(raw)

    class Boom:
        def classify(self, text):
            raise AssertionError("a duplicate must not reach the classifier")
    assert intake(raw, classifier=Boom(), duplicates={first.sha256}).result == DUPLICATE_DOCUMENT


class Scripted:
    def __init__(self, answer):
        self.answer = answer

    def classify(self, text):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.mark.parametrize("answer, result", [
    (ClassifierFailed("x"), DOCUMENT_UNREADABLE),                                   # no confident answer
    (Classification(True, None, date(2026, 9, 1), None), DOCUMENT_UNREADABLE),      # medical, no catalog type
    (Classification(True, "CBC", None, None), DOCUMENT_EXPIRED),                    # no date: fail closed
    (Classification(True, "CBC", date(2026, 9, 23), None), DOCUMENT_UNREADABLE),    # dated after today
    (Classification(True, "CBC", date(2026, 9, 1), "P-20000"), PATIENT_MISMATCH),   # another patient's
    (Classification(True, "CBC", date(2026, 9, 1), "P-10041"), ACCEPTED),           # the patient's own
    (Classification(True, "CBC", date(2026, 9, 1), "402781"), ACCEPTED),            # not the IdP shape: ignored
    (Classification(False, "CBC", date(2026, 9, 1), None), NON_MEDICAL_DOCUMENT),   # not medical wins
])
def test_each_classification_outcome(answer, result):
    assert intake(data("2026", "cbc"), classifier=Scripted(answer)).result == result


@pytest.mark.parametrize("doc_type, age_days, result", [
    ("CBC", 90, ACCEPTED), ("CBC", 91, DOCUMENT_EXPIRED),
    ("ECG", 180, ACCEPTED), ("ECG", 181, DOCUMENT_EXPIRED),
    ("PREOP_SUMMARY", 30, ACCEPTED), ("PREOP_SUMMARY", 31, DOCUMENT_EXPIRED),
])
def test_validity_is_per_type_and_inclusive(doc_type, age_days, result):
    from datetime import timedelta
    answer = Classification(True, doc_type, TODAY - timedelta(days=age_days), None)
    assert intake(data("2026", "cbc"), classifier=Scripted(answer)).result == result


def test_a_rejected_outcome_keeps_its_hash_and_size():
    outcome = intake(data("2026", "electricity_bill"))
    assert outcome.document_type is None and len(outcome.sha256) == 64 and outcome.size_bytes > 0
```

- [ ] **Step 2: Run to see them fail** (import errors).

- [ ] **Step 3: `app/classifier.py`:**

```python
"""Classifies a document's extracted text: medical or not, which catalog type, its date, and a
patient identifier if the text shows one (HospitalAgent design §4.3). Classification only - the
prompt forbids interpreting a result. Any unusable answer raises ClassifierFailed, which the
intake turns into DOCUMENT_UNREADABLE (fail closed)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from .catalog import CODES

MAX_TEXT_CHARS = 8000

PROMPT = (
    "You classify one document for a hospital's intake system. The text was extracted from a PDF "
    "and is usually Hebrew. Answer only with the JSON the schema allows.\n"
    "- is_medical: true only for a clinical document (lab results, a test report, a medical summary); "
    "false for bills, receipts, letters and anything else.\n"
    f"- document_type: one of {', '.join(CODES)} when the document is exactly that type, otherwise null. "
    "CBC = complete blood count; COAGULATION_TESTS = coagulation / PT / INR tests; ECG = electrocardiogram; "
    "URINALYSIS = urine test; PREOP_SUMMARY = a pre-operative assessment summary. A pre-operative summary "
    "that lists other tests is still PREOP_SUMMARY.\n"
    "- document_date: the date the document was issued (e.g. 'תאריך הפקה'), as YYYY-MM-DD, or null.\n"
    "- patient_identifier: an explicit patient identifier printed in the document, or null. Never a "
    "document, invoice or customer number.\n"
    "Never interpret results, never judge whether a value is normal, and never give medical advice."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["is_medical", "document_type", "document_date", "patient_identifier"],
    "properties": {
        "is_medical": {"type": "boolean"},
        "document_type": {"anyOf": [{"type": "string", "enum": list(CODES)}, {"type": "null"}]},
        "document_date": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "patient_identifier": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
}


class ClassifierFailed(Exception):
    """No usable classification. The reason is a code, never the document's text."""


@dataclass(frozen=True)
class Classification:
    is_medical: bool
    document_type: str | None
    document_date: date | None
    patient_identifier: str | None


class Classifier(Protocol):
    def classify(self, text: str) -> Classification: ...


def _parse(data: Any) -> Classification:
    if not isinstance(data, dict) or set(data) != set(SCHEMA["required"]):
        raise ClassifierFailed("shape")
    is_medical, doc_type, raw_date, identifier = (data["is_medical"], data["document_type"],
                                                  data["document_date"], data["patient_identifier"])
    if not isinstance(is_medical, bool):
        raise ClassifierFailed("is_medical")
    if doc_type is not None and doc_type not in CODES:
        raise ClassifierFailed("document_type")
    if raw_date is not None:
        if not isinstance(raw_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            raise ClassifierFailed("document_date")
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError:
            raise ClassifierFailed("document_date") from None
    else:
        parsed_date = None
    if identifier is not None and not isinstance(identifier, str):
        raise ClassifierFailed("patient_identifier")
    return Classification(is_medical, doc_type, parsed_date, identifier or None)


class OpenAIClassifier:
    """The same call the Hospital Agent makes: Chat Completions, reasoning_effort="none", a strict
    JSON Schema. The client is built lazily; tests inject one."""

    def __init__(self, api_key: str, model: str, *, client: Any = None, timeout_seconds: float = 30.0) -> None:
        self.model, self._api_key, self._timeout, self._client = model, api_key, timeout_seconds, client

    def __repr__(self) -> str:
        return f"OpenAIClassifier(model={self.model!r})"  # never the key

    def _openai(self) -> Any:
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self._api_key, timeout=self._timeout, max_retries=0)
        return self._client

    def classify(self, text: str) -> Classification:
        import openai

        try:
            response = self._openai().chat.completions.create(
                model=self.model,
                reasoning_effort="none",
                messages=[{"role": "system", "content": PROMPT},
                          {"role": "user", "content": text[:MAX_TEXT_CHARS]}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "document_classification", "strict": True, "schema": SCHEMA}},
            )
            data = json.loads(response.choices[0].message.content)
        except openai.OpenAIError as exc:
            raise ClassifierFailed(f"api:{type(exc).__name__}") from None
        except (json.JSONDecodeError, TypeError, IndexError, AttributeError):
            raise ClassifierFailed("unparsable") from None
        return _parse(data)


# The fake: deterministic keyword rules over the demo files' Hebrew. Order matters - a
# pre-operative summary lists the other tests, and the urine test mentions "טרום-ניתוח".
_RULES: tuple[tuple[str, str], ...] = (
    ("סיכום הערכה", "PREOP_SUMMARY"),
    ("בדיקת שתן", "URINALYSIS"),
    ("חשמלית של הלב", "ECG"),
    ("בדיקות קרישה", "COAGULATION_TESTS"),
    ("ספירת דם", "CBC"),
)
_NON_MEDICAL = ("שאינו רפואי", "לא מסמך רפואי", "חשבון חשמל")


class FakeClassifier:
    """For the tests and offline runs only."""

    def classify(self, text: str) -> Classification:
        if not text.strip():
            raise ClassifierFailed("empty")
        found = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
        doc_date = date(int(found.group(3)), int(found.group(2)), int(found.group(1))) if found else None
        identifier = re.search(r"\bP-\d{4,}\b", text)
        patient = identifier.group(0) if identifier else None
        if any(word in text for word in _NON_MEDICAL):
            return Classification(False, None, doc_date, patient)
        for word, code in _RULES:
            if word in text:
                return Classification(True, code, doc_date, patient)
        return Classification(True, None, doc_date, patient)
```

- [ ] **Step 4: `app/intake.py`:**

```python
"""The intake, in the design's order (HospitalAgent design §4.2) - the first failure decides."""
from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import pypdf

from .catalog import BY_CODE
from .classifier import Classifier, ClassifierFailed

ACCEPTED = "ACCEPTED"
NON_MEDICAL_DOCUMENT = "NON_MEDICAL_DOCUMENT"
DOCUMENT_UNREADABLE = "DOCUMENT_UNREADABLE"
DOCUMENT_EXPIRED = "DOCUMENT_EXPIRED"
DUPLICATE_DOCUMENT = "DUPLICATE_DOCUMENT"
PATIENT_MISMATCH = "PATIENT_MISMATCH"

# Only an identifier of the demo IdP's shape is compared; any other number the model reports
# (a document or customer number) is ignored rather than guessed at (design §4.2).
_IDP_SHAPE = re.compile(r"P-\d+")


@dataclass(frozen=True)
class IntakeOutcome:
    result: str
    document_type: str | None
    document_date: date | None
    sha256: str
    size_bytes: int


def _text_of(data: bytes) -> str | None:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "".join(page.extract_text() or "" for page in reader.pages)
    except Exception:  # any parse failure is "unreadable", never a crash
        return None


def run_intake(data: bytes, patient_id: str, *, classifier: Classifier, today: date,
               is_duplicate: Callable[[str], bool], max_bytes: int) -> IntakeOutcome:
    sha = hashlib.sha256(data).hexdigest()

    def outcome(result: str, doc_type: str | None = None, doc_date: date | None = None) -> IntakeOutcome:
        return IntakeOutcome(result, doc_type, doc_date, sha, len(data))

    # 1. size and signature
    if len(data) > max_bytes or not data.startswith(b"%PDF-"):
        return outcome(DOCUMENT_UNREADABLE)
    # 2. parse and extract text
    text = _text_of(data)
    if not text or not text.strip():
        return outcome(DOCUMENT_UNREADABLE)
    # 3. an accepted duplicate (a rejected file may be tried again)
    if is_duplicate(sha):
        return outcome(DUPLICATE_DOCUMENT)
    # 4. classification
    try:
        found = classifier.classify(text)
    except ClassifierFailed:
        return outcome(DOCUMENT_UNREADABLE)
    if not found.is_medical:
        return outcome(NON_MEDICAL_DOCUMENT, None, found.document_date)
    doc_type = BY_CODE.get(found.document_type or "")
    if doc_type is None:
        return outcome(DOCUMENT_UNREADABLE, None, found.document_date)
    # 5. the patient's own
    if found.patient_identifier and _IDP_SHAPE.fullmatch(found.patient_identifier) \
            and found.patient_identifier != patient_id:
        return outcome(PATIENT_MISMATCH, doc_type.code, found.document_date)
    # 6. validity (no date is expired; a date after today cannot be right)
    if found.document_date is None:
        return outcome(DOCUMENT_EXPIRED, doc_type.code, None)
    if found.document_date > today:
        return outcome(DOCUMENT_UNREADABLE, doc_type.code, found.document_date)
    if (today - found.document_date).days > doc_type.max_age_days:
        return outcome(DOCUMENT_EXPIRED, doc_type.code, found.document_date)
    return outcome(ACCEPTED, doc_type.code, found.document_date)
```

- [ ] **Step 5: Build the classifier from the environment** - in `app/main.py`, add `from .classifier import OpenAIClassifier` and replace the line
`app.state.classifier = None if classifier is FROM_ENV else classifier  # Task 3 builds it from env` with:

```python
    if classifier is FROM_ENV:
        classifier = (OpenAIClassifier(settings.openai_api_key, settings.openai_model)
                      if settings.openai_api_key else None)
    app.state.classifier = classifier
```

Add to `tests/test_service.py`:

```python
def test_the_classifier_comes_from_the_environment(tmp_path, monkeypatch):
    from app.classifier import OpenAIClassifier
    assert make(tmp_path).state.classifier is None           # conftest cleared OPENAI_API_KEY
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    built = make(tmp_path).state.classifier
    assert isinstance(built, OpenAIClassifier) and "sk-test" not in repr(built)
```

- [ ] **Step 6: Run** - all pass (18 + the new ones).

- [ ] **Step 7: Commit** - `Classify documents and run the intake checks`.

---

### Task 4: Storage, the two document routes and the audit

**Files:**
- Create: `app/storage.py`
- Modify: `app/main.py` (build the store from the environment; `POST` and `GET /api/v1/patients/{patient_id}/documents`)
- Test: `tests/test_storage.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: Task 3's `run_intake`, result constants, `FakeClassifier`; Task 1's models and `write_audit`.
- Produces: `storage.ObjectStore` (Protocol: `put(key: str, data: bytes) -> None`), `storage.StorageFailed`, `storage.InMemoryObjectStore` (`.objects: dict[str, bytes]`), `storage.S3ObjectStore(bucket: str, region: str, *, client=None)`; routes as the design's §4.1.

- [ ] **Step 1: Write the failing tests.**

`tests/test_storage.py`:
```python
import boto3
import pytest
from botocore.stub import Stubber

from app.storage import InMemoryObjectStore, S3ObjectStore, StorageFailed


def test_in_memory_store_keeps_what_it_is_given():
    store = InMemoryObjectStore()
    store.put("patients/P-1/DOC-1.pdf", b"%PDF-1")
    assert store.objects == {"patients/P-1/DOC-1.pdf": b"%PDF-1"}


def s3_client():
    return boto3.client("s3", region_name="eu-north-1", aws_access_key_id="x", aws_secret_access_key="y")


def test_s3_puts_a_private_encrypted_pdf():
    client = s3_client()
    with Stubber(client) as stub:
        stub.add_response("put_object", {}, {"Bucket": "b", "Key": "patients/P-1/DOC-1.pdf", "Body": b"%PDF",
                                              "ContentType": "application/pdf", "ServerSideEncryption": "AES256"})
        S3ObjectStore("b", "eu-north-1", client=client).put("patients/P-1/DOC-1.pdf", b"%PDF")
        stub.assert_no_pending_responses()


def test_an_s3_failure_is_storage_failed():
    client = s3_client()
    with Stubber(client) as stub:
        stub.add_client_error("put_object", service_error_code="AccessDenied", http_status_code=403)
        with pytest.raises(StorageFailed):
            S3ObjectStore("b", "eu-north-1", client=client).put("k", b"%PDF")


def test_the_store_never_shows_credentials():
    assert "y" not in repr(S3ObjectStore("bucket-name", "eu-north-1", client=s3_client())).replace("eu-north-1", "")
```

`tests/test_api.py`:
```python
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.classifier import FakeClassifier
from app.main import create_app
from app.models import AuditLog, Document
from app.storage import InMemoryObjectStore, StorageFailed

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "test-key"
H = {"X-API-Key": KEY}


def pdf(year, name):
    return (FIXTURES / year / f"{name}.pdf").read_bytes()


def make(tmp_path, *, store=None, classifier=None, **kwargs):
    store = InMemoryObjectStore() if store is None else store
    app = create_app(f"sqlite:///{(tmp_path / 'd.db').as_posix()}", api_key=KEY,
                     classifier=classifier or FakeClassifier(), store=store, today=lambda: date(2026, 9, 22), **kwargs)
    return app, store


def upload(client, name, year="2026", patient="P-10041", headers=H):
    return client.post(f"/api/v1/patients/{patient}/documents", headers=headers,
                       files={"file": (f"{name}.pdf", pdf(year, name), "application/pdf")})


def rows(app, model):
    with app.state.SessionLocal() as session:
        return list(session.scalars(select(model)))


def test_an_accepted_document_is_stored_and_listed(tmp_path):
    app, store = make(tmp_path)
    with TestClient(app) as client:
        response = upload(client, "cbc")
        listing = client.get("/api/v1/patients/P-10041/documents", headers=H).json()
    assert response.status_code == 201
    body = response.json()
    assert re.fullmatch(r"DOC-[0-9A-F]{12}", body["document_id"])
    assert body == {"document_id": body["document_id"], "document_type": "CBC",
                    "document_date": "2026-09-15", "result": "ACCEPTED"}
    assert list(store.objects) == [f"patients/P-10041/{body['document_id']}.pdf"]
    assert store.objects[f"patients/P-10041/{body['document_id']}.pdf"] == pdf("2026", "cbc")
    [doc] = listing["documents"]
    assert {k: doc[k] for k in ("document_id", "document_type", "document_date", "result")} == {
        "document_id": body["document_id"], "document_type": "CBC", "document_date": "2026-09-15", "result": "ACCEPTED"}
    assert "uploaded_at" in doc


@pytest.mark.parametrize("name, year, result", [
    ("electricity_bill", "2026", "NON_MEDICAL_DOCUMENT"),
    ("cbc", "2024", "DOCUMENT_EXPIRED"),
])
def test_a_rejected_document_is_recorded_but_never_stored(tmp_path, name, year, result):
    app, store = make(tmp_path)
    with TestClient(app) as client:
        body = upload(client, name, year).json()
    assert body["result"] == result
    assert store.objects == {}
    [row] = rows(app, Document)
    assert row.result == result and row.s3_object_key is None


def test_the_same_accepted_file_again_is_a_duplicate_and_a_rejected_one_is_checked_afresh(tmp_path):
    app, store = make(tmp_path)
    with TestClient(app) as client:
        assert upload(client, "cbc").json()["result"] == "ACCEPTED"
        assert upload(client, "cbc").json()["result"] == "DUPLICATE_DOCUMENT"
        assert upload(client, "electricity_bill").json()["result"] == "NON_MEDICAL_DOCUMENT"
        assert upload(client, "electricity_bill").json()["result"] == "NON_MEDICAL_DOCUMENT"
        # Another patient uploading the same file is not a duplicate of the first patient's.
        assert upload(client, "cbc", patient="P-20000").json()["result"] == "ACCEPTED"
    assert len(store.objects) == 2


def test_the_six_demo_files_give_the_designs_results(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as client:
        results = {name: upload(client, name).json() for name in
                   ("cbc", "coagulation", "ecg", "urinalysis", "preop_summary", "electricity_bill")}
    assert {n: (r["result"], r["document_type"]) for n, r in results.items()} == {
        "cbc": ("ACCEPTED", "CBC"), "coagulation": ("ACCEPTED", "COAGULATION_TESTS"), "ecg": ("ACCEPTED", "ECG"),
        "urinalysis": ("ACCEPTED", "URINALYSIS"), "preop_summary": ("ACCEPTED", "PREOP_SUMMARY"),
        "electricity_bill": ("NON_MEDICAL_DOCUMENT", None)}


def test_a_patient_sees_only_their_own_documents(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as client:
        upload(client, "cbc", patient="P-10041")
        upload(client, "ecg", patient="P-20000")
        mine = client.get("/api/v1/patients/P-10041/documents", headers=H).json()["documents"]
    assert [d["document_type"] for d in mine] == ["CBC"]


def test_every_call_needs_the_key(tmp_path):
    app, store = make(tmp_path)
    with TestClient(app) as client:
        assert upload(client, "cbc", headers={}).status_code == 401
        assert upload(client, "cbc", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/api/v1/patients/P-10041/documents").status_code == 401
    assert store.objects == {} and rows(app, Document) == []


def test_an_invalid_patient_id_is_refused(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as client:
        assert upload(client, "cbc", patient="P 1").status_code == 400
        assert client.get("/api/v1/patients/%20/documents", headers=H).status_code == 400


def test_a_missing_file_is_refused(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/v1/patients/P-10041/documents", headers=H).status_code == 400


def test_without_a_classifier_or_a_store_nothing_is_accepted(tmp_path):
    for kwargs in ({"classifier": None}, {"store": None}):
        app = create_app(f"sqlite:///{(tmp_path / f'{len(kwargs)}{list(kwargs)[0]}.db').as_posix()}", api_key=KEY,
                         classifier=kwargs.get("classifier", FakeClassifier()), store=kwargs.get("store", InMemoryObjectStore()),
                         today=lambda: date(2026, 9, 22))
        with TestClient(app) as client:
            response = upload(client, "cbc")
        assert response.status_code == 503 and response.json() == {"error": "service_not_configured"}
        assert rows(app, Document) == []


def test_a_storage_failure_stores_nothing_and_says_so(tmp_path):
    class Failing:
        def put(self, key, data):
            raise StorageFailed("AccessDenied")
    app, _ = make(tmp_path, store=Failing())
    with TestClient(app) as client:
        response = upload(client, "cbc")
    assert response.status_code == 503 and response.json() == {"error": "storage_unavailable"}
    assert rows(app, Document) == []
    assert [(a.operation, a.result) for a in rows(app, AuditLog)] == [("UploadDocument", "storage_unavailable")]


def test_the_audit_holds_codes_and_ids_only(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as client:
        doc_id = upload(client, "cbc").json()["document_id"]
        client.get("/api/v1/patients/P-10041/documents", headers=H)
    audit = rows(app, AuditLog)
    assert [(a.operation, a.result, a.document_id, a.patient_id) for a in audit] == [
        ("UploadDocument", "ACCEPTED", doc_id, "P-10041"), ("ListDocuments", "ok", None, "P-10041")]


def test_the_log_never_holds_the_patient_id_or_a_file_name(tmp_path, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="document-service")
    app, _ = make(tmp_path)
    with TestClient(app) as client:
        upload(client, "cbc")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "P-10041" not in logged and "cbc.pdf" not in logged


def test_the_documents_routes_are_the_whole_api(tmp_path):
    app, _ = make(tmp_path)
    paths = app.openapi()["paths"]
    assert set(paths) == {"/health", "/api/v1/patients/{patient_id}/documents"}
    assert set(paths["/api/v1/patients/{patient_id}/documents"]) == {"get", "post"}
```

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: `app/storage.py`:**

```python
"""Where accepted PDFs go (HospitalAgent design §4.4): a private S3 bucket, or memory in tests."""
from __future__ import annotations

from typing import Any, Protocol


class StorageFailed(Exception):
    """The file could not be stored. The reason is an error code, never the content."""


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...


class InMemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data


class S3ObjectStore:
    """Private, encrypted at rest (SSE-S3), over TLS (boto3's default endpoint is https). The IAM
    user behind the credentials may only PutObject/GetObject under patients/* (design §4.4)."""

    def __init__(self, bucket: str, region: str, *, client: Any = None) -> None:
        self.bucket, self.region = bucket, region
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client("s3", region_name=region,
                                  config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}))
        self._client = client

    def __repr__(self) -> str:
        return f"S3ObjectStore(bucket={self.bucket!r}, region={self.region!r})"  # never credentials

    def put(self, key: str, data: bytes) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data,
                                    ContentType="application/pdf", ServerSideEncryption="AES256")
        except ClientError as exc:
            raise StorageFailed(exc.response.get("Error", {}).get("Code", "ClientError")) from None
        except BotoCoreError as exc:
            raise StorageFailed(type(exc).__name__) from None
```

- [ ] **Step 4: `app/main.py`** - add imports `from fastapi import File, Path as ApiPath, UploadFile`, `from sqlalchemy import select`, `from .intake import ACCEPTED, run_intake`, `from .models import Document`, `from .storage import S3ObjectStore, StorageFailed`; build the store (replace the Task-1 `app.state.store = ...` line):

```python
    if store is FROM_ENV:
        store = S3ObjectStore(settings.s3_bucket, settings.aws_region) if settings.s3_bucket else None
    app.state.store = store
```

and add, before `return app`:

```python
    def unauthorised() -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": "unauthorized"}, headers={"WWW-Authenticate": "ApiKey"})

    def elapsed(started: float) -> int:
        return round((time.perf_counter() - started) * 1000)

    @app.post("/api/v1/patients/{patient_id}/documents", status_code=201, tags=["Documents"])
    def upload_document(request: Request, patient_id: str = ApiPath(pattern=PATIENT_ID_PATTERN),
                        file: UploadFile = File(...)) -> JSONResponse:
        """Checks and classifies one PDF; stores it only if accepted (design §4.2)."""
        if not authorised(request):
            return unauthorised()
        state = request.app.state
        if state.classifier is None or state.store is None:
            return JSONResponse(status_code=503, content={"error": "service_not_configured"})
        started = time.perf_counter()
        limit = state.settings.max_upload_bytes
        data = file.file.read(limit + 1)  # one byte over the limit is enough to know it is too big
        with state.SessionLocal() as session:
            def accepted_duplicate(sha: str) -> bool:
                return session.scalar(select(Document.document_id).where(
                    Document.patient_id == patient_id, Document.sha256 == sha, Document.result == ACCEPTED)
                    .limit(1)) is not None

            outcome = run_intake(data, patient_id, classifier=state.classifier, today=state.today(),
                                 is_duplicate=accepted_duplicate, max_bytes=limit)
            document_id = f"DOC-{secrets.token_hex(6).upper()}"
            key = None
            if outcome.result == ACCEPTED:
                key = f"patients/{patient_id}/{document_id}.pdf"
                try:
                    state.store.put(key, data)
                except StorageFailed:
                    write_audit(session, patient_id=patient_id, operation="UploadDocument",
                                result="storage_unavailable", document_id=None, latency_ms=elapsed(started))
                    return JSONResponse(status_code=503, content={"error": "storage_unavailable"})
            session.add(Document(document_id=document_id, patient_id=patient_id, document_type=outcome.document_type,
                                 document_date=outcome.document_date, result=outcome.result, sha256=outcome.sha256,
                                 size_bytes=outcome.size_bytes, s3_object_key=key))
            write_audit(session, patient_id=patient_id, operation="UploadDocument", result=outcome.result,
                        document_id=document_id, latency_ms=elapsed(started))
        return JSONResponse(status_code=201, content={
            "document_id": document_id, "document_type": outcome.document_type,
            "document_date": outcome.document_date.isoformat() if outcome.document_date else None,
            "result": outcome.result})

    @app.get("/api/v1/patients/{patient_id}/documents", tags=["Documents"])
    def list_documents(request: Request, patient_id: str = ApiPath(pattern=PATIENT_ID_PATTERN)) -> JSONResponse:
        """The patient's documents, oldest first, with their intake results (for CheckDocuments)."""
        if not authorised(request):
            return unauthorised()
        started = time.perf_counter()
        with request.app.state.SessionLocal() as session:
            documents = list(session.scalars(select(Document).where(Document.patient_id == patient_id)
                                             .order_by(Document.uploaded_at, Document.document_id)))
            write_audit(session, patient_id=patient_id, operation="ListDocuments", result="ok",
                        document_id=None, latency_ms=elapsed(started))
        return JSONResponse({"documents": [{
            "document_id": d.document_id, "document_type": d.document_type,
            "document_date": d.document_date.isoformat() if d.document_date else None,
            "result": d.result, "uploaded_at": d.uploaded_at.isoformat()} for d in documents]})
```

A missing `file` is a validation error -> `400` through the existing handler.

- [ ] **Step 5: Add a test for the store from the environment** to `tests/test_service.py`:

```python
def test_the_store_comes_from_the_environment(tmp_path, monkeypatch):
    from app.storage import S3ObjectStore
    assert make(tmp_path).state.store is None
    monkeypatch.setenv("S3_BUCKET", "hospital-docs-test")
    built = make(tmp_path).state.store
    assert isinstance(built, S3ObjectStore) and built.bucket == "hospital-docs-test" and built.region == "eu-north-1"
```

- [ ] **Step 6: Run** - all pass.

- [ ] **Step 7: Commit** - `Store accepted documents in S3 and serve the patient's documents`.

---

### Task 5: The README, the AWS setup, and the live tests

**Files:** Create `README.md`, `tests/test_live.py`.

- [ ] **Step 1: `tests/test_live.py`** (skipped unless asked; they reach OpenAI / AWS with the owner's own `.env` values, passed with `--env-file .env` by the owner):

```python
"""Live checks - they run only when asked, with the service's own credentials:
    docker run ... --env-file .env -e RUN_LIVE_LLM=1 ... pytest tests/test_live.py
They are the only tests that reach the network."""
import os
from pathlib import Path

import pypdf
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
live_llm = pytest.mark.skipif(os.getenv("RUN_LIVE_LLM") != "1", reason="set RUN_LIVE_LLM=1 to call OpenAI")
live_s3 = pytest.mark.skipif(os.getenv("RUN_LIVE_S3") != "1", reason="set RUN_LIVE_S3=1 to write to the bucket")


@pytest.fixture
def real_env(monkeypatch):
    """conftest clears the live variables; these tests put back what the owner passed in."""
    return os.environ.get


@live_llm
@pytest.mark.parametrize("name, expected", [("cbc", "CBC"), ("coagulation", "COAGULATION_TESTS"), ("ecg", "ECG"),
                                            ("urinalysis", "URINALYSIS"), ("preop_summary", "PREOP_SUMMARY"),
                                            ("electricity_bill", None)])
def test_the_real_model_classifies_the_demo_files(name, expected):
    from app.classifier import OpenAIClassifier
    key = os.environ["LIVE_OPENAI_API_KEY"]
    text = "".join(p.extract_text() or "" for p in pypdf.PdfReader(FIXTURES / "2026" / f"{name}.pdf").pages)
    got = OpenAIClassifier(key, os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")).classify(text)
    assert got.document_type == expected and got.is_medical == (expected is not None)


@live_s3
def test_the_real_bucket_accepts_a_private_encrypted_put():
    from app.storage import S3ObjectStore
    S3ObjectStore(os.environ["LIVE_S3_BUCKET"], os.environ.get("AWS_REGION", "eu-north-1")).put(
        "patients/LIVE-TEST/DOC-LIVE.pdf", (FIXTURES / "2026" / "cbc.pdf").read_bytes())
```

Because `conftest.py` clears `OPENAI_API_KEY` and `S3_BUCKET`, the live tests read `LIVE_OPENAI_API_KEY` / `LIVE_S3_BUCKET`; the README's command maps them (`-e LIVE_OPENAI_API_KEY="$OPENAI_API_KEY"` is **not** used - the owner runs `docker compose run --rm -e RUN_LIVE_LLM=1 -e LIVE_OPENAI_API_KEY -e LIVE_S3_BUCKET api ...`? No: the image has no tests). Use this documented command instead, which never prints a secret:

```
MSYS_NO_PATHCONV=1 docker run --rm --env-file .env -v "$(pwd -W):/src" -w /src python:3.12-slim sh -c 'pip install -q -r requirements-dev.txt && LIVE_OPENAI_API_KEY="$OPENAI_API_KEY" LIVE_S3_BUCKET="$S3_BUCKET" RUN_LIVE_LLM=1 RUN_LIVE_S3=1 pytest -q -p no:cacheprovider tests/test_live.py'
```

(the variables are copied inside the container before pytest's conftest clears the originals). Remove the unused `real_env` fixture if it stays unused.

- [ ] **Step 2: `README.md`** (Hebrew, like `appointment-service`'s), sections:
  - **מה השירות עושה** - intake, classification, private storage, listing; the six result codes and what each means (the design's table).
  - **הפעלה** - `.env` from `.env.example`; `docker compose up --build -d`; `http://localhost:8090/health`, `/docs`; the service listens on 127.0.0.1 only.
  - **הקמת S3 ו-IAM (פעם אחת, ב-AWS Console)** - exact steps: (1) S3 -> Create bucket, name e.g. `hospital-agent-documents-<suffix>`, region `eu-north-1`, *Block all public access* on, default encryption SSE-S3, versioning optional; (2) Permissions -> Bucket policy denying non-TLS:
    ```json
    {"Version":"2012-10-17","Statement":[{"Sid":"DenyInsecureTransport","Effect":"Deny","Principal":"*",
      "Action":"s3:*","Resource":["arn:aws:s3:::BUCKET","arn:aws:s3:::BUCKET/*"],
      "Condition":{"Bool":{"aws:SecureTransport":"false"}}}]}
    ```
    (3) IAM -> Users -> Create user `document-service` (no console access); attach an inline policy:
    ```json
    {"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:PutObject","s3:GetObject"],
      "Resource":"arn:aws:s3:::BUCKET/patients/*"}]}
    ```
    (4) Security credentials -> Create access key (use case: application running outside AWS); put `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET` into `.env` - never into the repo.
  - **מפתח OpenAI** - `OPENAI_API_KEY` in `.env`; what is sent (the extracted text, up to 8,000 characters; classification only).
  - **API** - the two routes with a `curl` example each (`X-API-Key`), the response shapes.
  - **קבצי הדמו** - pointer to `demo/README.md`.
  - **בדיקות** - the standard command; the live command above.
  - **פרטיות** - only accepted files are stored; rejected ones keep metadata and hash only; the log never holds a patient id or a file name; audit holds codes and ids.

- [ ] **Step 3: Run** - the full suite passes; `tests/test_live.py` reports its tests as skipped.

- [ ] **Step 4: Commit** - `Document the service, the AWS setup and the live checks`.
