"""The demo IdP: a fixed user list and HMAC-signed tokens (spec §18.3, design §3).

There is no session on the server and no real SSO: authenticate() checks a password
against DEMO_USERS, and issue_token()/verify_token() carry the identity in a signed,
stateless token. The server is the only place that sets reviewer_id, reviewer_role and
a patient's patient_id - they come from the verified token, never from the request body.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256

PATIENT, CLINICAL_STAFF, ADMIN_STAFF = "patient", "clinical_staff", "admin_staff"

# Must equal guards.REVIEWER_ROLES - tests/test_auth.py checks this.
STAFF_ROLES = frozenset({CLINICAL_STAFF, ADMIN_STAFF})

_DEFAULT_AUTH_SECRET = "dev-only-insecure-demo-secret-do-not-use-in-production"
_DEFAULT_DEMO_PASSWORD = "demo"


@dataclass(frozen=True)
class DemoUser:
    """One row of the fixed demo user list (§18.3)."""

    user_id: str
    role: str
    display_name: str
    identity_verified: bool = True


# §18.3: the fixed demo user list. Staff names match policy/rules.pl (§10).
DEMO_USERS: dict[str, DemoUser] = {
    "P-10041": DemoUser("P-10041", PATIENT, "דנה כהן", identity_verified=True),
    "P-20000": DemoUser("P-20000", PATIENT, "יוסי לוי", identity_verified=True),
    # PatientVerificationFailed path (§3, §12.4): this patient's identity does not verify.
    "P-30000": DemoUser("P-30000", PATIENT, "מיכל אברהם", identity_verified=False),
    "coordinator_nurse": DemoUser("coordinator_nurse", CLINICAL_STAFF, "אחות מתאמת"),
    "admin_coordinator": DemoUser("admin_coordinator", ADMIN_STAFF, "רכזת מנהלה"),
}


@dataclass(frozen=True)
class Principal:
    """The identity a verified token carries - never taken from the request body."""

    user_id: str
    role: str
    display_name: str

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES

    @property
    def patient_id(self) -> str | None:
        return None if self.is_staff else self.user_id


def auth_secret(env: Mapping[str, str] = os.environ) -> str:
    """The HMAC signing key: AUTH_SECRET, or a fixed insecure default for the demo."""
    return env.get("AUTH_SECRET") or _DEFAULT_AUTH_SECRET


def demo_password(env: Mapping[str, str] = os.environ) -> str:
    """The one password shared by every demo user: DEMO_PASSWORD, else "demo"."""
    return env.get("DEMO_PASSWORD") or _DEFAULT_DEMO_PASSWORD


def authenticate(user_id: str, password: str, *, env: Mapping[str, str] = os.environ) -> DemoUser | None:
    """Check `password` against the one demo password. None for an unknown user or a wrong password."""
    user = DEMO_USERS.get(user_id)
    if user is None:
        return None
    expected = demo_password(env)
    if not hmac.compare_digest(password.encode("utf-8"), expected.encode("utf-8")):
        return None
    return user


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text.encode("ascii") + padding.encode("ascii"))


def _sign(payload_b64: str, secret: str) -> str:
    signature = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), sha256).digest()
    return _b64url_encode(signature)


def issue_token(user: DemoUser, *, now: datetime, secret: str, ttl: timedelta = timedelta(hours=8)) -> str:
    """base64url(json{sub, role, exp}) + "." + base64url(hmac_sha256(secret, that part)) - §18.3."""
    payload = {"sub": user.user_id, "role": user.role, "exp": int((now + ttl).timestamp())}
    payload_b64 = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


def verify_token(token: str, *, now: datetime, secret: str) -> Principal | None:
    """The verified Principal, or None - never raises. See auth.py module docstring."""
    try:
        payload_b64, signature_b64 = token.split(".")
        expected_signature_b64 = _sign(payload_b64, secret)
        if not hmac.compare_digest(signature_b64.encode("ascii"), expected_signature_b64.encode("ascii")):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        sub, role, exp = payload["sub"], payload["role"], payload["exp"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(exp, int) or exp <= int(now.timestamp()):
        return None

    user = DEMO_USERS.get(sub)
    if user is None or user.role != role:
        return None

    return Principal(user_id=user.user_id, role=user.role, display_name=user.display_name)
