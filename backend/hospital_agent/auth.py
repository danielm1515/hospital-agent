"""The demo IdP: the users table and HMAC-signed tokens (spec §18.3, design §3).

There is no session on the server and no real SSO: authenticate() checks a password against
the user's own scrypt hash in the `users` table (migration 0009, docs/spec_corrections.md row
97), and issue_token()/verify_token() carry the identity in a signed, stateless token. The
server is the only place that sets reviewer_id, reviewer_role and a patient's patient_id - they
come from the verified token, never from the request body.

verify_token() re-reads the user on every request, so a user made inactive, or whose role
changed, is refused at once - the token alone is never enough.
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
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.engine import Engine

from .db import users as users_table
from .passwords import DUMMY_HASH, verify_password

PATIENT, CLINICAL_STAFF, ADMIN_STAFF = "patient", "clinical_staff", "admin_staff"

# Must equal guards.REVIEWER_ROLES - tests/test_auth.py checks this.
STAFF_ROLES = frozenset({CLINICAL_STAFF, ADMIN_STAFF})

_DEFAULT_AUTH_SECRET = "dev-only-insecure-demo-secret-do-not-use-in-production"
_DEFAULT_DEMO_PASSWORD = "demo"


@dataclass(frozen=True)
class User:
    """One row of the `users` table (migration 0009). Staff ids match policy/rules.pl (§10)."""

    user_id: str
    role: str
    display_name: str
    password_hash: str
    identity_verified: bool = True
    active: bool = True


class UserStore(Protocol):
    def get(self, user_id: str) -> User | None: ...


class DbUserStore:
    """The users table, read as hospital_app (SELECT only - the application never writes a user)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, user_id: str) -> User | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(users_table).where(users_table.c.user_id == user_id)).first()
        if row is None:
            return None
        return User(user_id=row.user_id, role=row.role, display_name=row.display_name,
                    password_hash=row.password_hash, identity_verified=row.identity_verified,
                    active=row.active)


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
    """The password migration 0009 hashed for every seeded user: DEMO_PASSWORD, else "demo"."""
    return env.get("DEMO_PASSWORD") or _DEFAULT_DEMO_PASSWORD


def authenticate(store: UserStore, user_id: str, password: str) -> User | None:
    """The user, if `password` matches its own hash and the user is active; None otherwise.

    An unknown or inactive user is still checked against a dummy hash, so how long a login
    takes does not tell which user ids exist - the same answer, at the same cost."""
    user = store.get(user_id)
    matches = verify_password(password, user.password_hash if user else DUMMY_HASH)
    return user if user is not None and user.active and matches else None


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text.encode("ascii") + padding.encode("ascii"))


def _sign(payload_b64: str, secret: str) -> str:
    signature = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), sha256).digest()
    return _b64url_encode(signature)


def issue_token(user: User, *, now: datetime, secret: str, ttl: timedelta = timedelta(hours=8)) -> str:
    """base64url(json{sub, role, exp}) + "." + base64url(hmac_sha256(secret, that part)) - §18.3."""
    payload = {"sub": user.user_id, "role": user.role, "exp": int((now + ttl).timestamp())}
    payload_b64 = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


def verify_token(token: str, *, now: datetime, secret: str, store: UserStore) -> Principal | None:
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

    user = store.get(sub) if isinstance(sub, str) else None
    if user is None or not user.active or user.role != role:
        return None

    return Principal(user_id=user.user_id, role=user.role, display_name=user.display_name)
