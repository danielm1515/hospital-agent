"""The users table: the IdP's users, their role and a password hash each (docs/spec_corrections.md
row 97, which supersedes row 66's "DEMO_USERS stays the IdP").

The same five users the fixed list in auth.py held - three patients and the two staff users -
with the same ids, roles, names and identity verdicts (P-30000 still fails verification on
purpose). Each gets its own scrypt hash of the demo password (DEMO_PASSWORD at the time this
migration runs, else "demo"), so logging in works exactly as before; a password is changed by
updating that one row's hash, never by editing this list.

`identity_verified` and `password_hash` are IdP data, not patient attributes another system
should see: hospital_app gets SELECT only (the application never writes a user), and
hospital_reader - the appointment-service's role, which reads `patients` - gets nothing on this
table. A patient user's id is its `patients.patient_id`; tests/test_users.py fails if the two
lists of patients ever disagree.

The hash format is passwords.py's, written inline here: a migration must not import application
code that may change after it ran.

Revision ID: 0009
"""
import base64
import hashlib
import os

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

APP_ROLE = "hospital_app"
ROLES = ("patient", "clinical_staff", "admin_staff")

USERS = [
    {"user_id": "P-10041", "role": "patient", "display_name": "דנה כהן", "identity_verified": True},
    {"user_id": "P-20000", "role": "patient", "display_name": "יוסי לוי", "identity_verified": True},
    # PatientVerificationFailed path (§3, §12.4): this patient's identity does not verify.
    {"user_id": "P-30000", "role": "patient", "display_name": "מיכל אברהם", "identity_verified": False},
    {"user_id": "coordinator_nurse", "role": "clinical_staff", "display_name": "אחות מתאמת",
     "identity_verified": True},
    {"user_id": "admin_coordinator", "role": "admin_staff", "display_name": "רכזת מנהלה",
     "identity_verified": True},
]


def _hash(password: str) -> str:
    """passwords.hash_password's format: scrypt$n$r$p$salt$key, n=2**14, r=8, p=1."""
    salt = os.urandom(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${2**14}$8$1${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"


def upgrade() -> None:
    users = op.create_table(
        "users",
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("identity_verified", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"role IN ({', '.join(repr(r) for r in ROLES)})", name="ck_users_role"),
    )
    password = os.environ.get("DEMO_PASSWORD") or "demo"
    op.bulk_insert(users, [{**user, "password_hash": _hash(password)} for user in USERS])
    op.execute(f"GRANT SELECT ON users TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("users")
