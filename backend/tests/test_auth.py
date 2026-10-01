"""The demo IdP: the users table and HMAC tokens (spec §18.3, design §3, row 97).

These are the token and password logic over an in-memory UserStore; the users table itself
(migration 0009, grants, seed) is tests/test_users.py.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from hospital_agent.auth import (
    ADMIN_STAFF,
    CLINICAL_STAFF,
    PATIENT,
    STAFF_ROLES,
    Principal,
    User,
    auth_secret,
    authenticate,
    demo_password,
    issue_token,
    verify_token,
)
from hospital_agent.guards import REVIEWER_ROLES
from hospital_agent.passwords import hash_password

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
SECRET = "test-secret"

DANA = User("P-10041", PATIENT, "דנה כהן", hash_password("dana-pw"))
NURSE = User("coordinator_nurse", CLINICAL_STAFF, "אחות מתאמת", hash_password("nurse-pw"))


class Store:
    """An in-memory UserStore."""

    def __init__(self, *users: User) -> None:
        self.users = {user.user_id: user for user in users}

    def get(self, user_id: str) -> User | None:
        return self.users.get(user_id)


STORE = Store(DANA, NURSE)


def test_staff_roles_matches_guards_reviewer_roles():
    assert STAFF_ROLES == REVIEWER_ROLES
    assert STAFF_ROLES == frozenset({CLINICAL_STAFF, ADMIN_STAFF})


# --- authenticate -----------------------------------------------------------------


def test_authenticate_checks_the_users_own_password():
    assert authenticate(STORE, "P-10041", "dana-pw") == DANA
    assert authenticate(STORE, "coordinator_nurse", "nurse-pw") == NURSE


def test_authenticate_refuses_another_users_password():
    assert authenticate(STORE, "P-10041", "nurse-pw") is None


def test_authenticate_wrong_password_returns_none():
    assert authenticate(STORE, "P-10041", "wrong") is None


def test_authenticate_unknown_user_returns_none():
    assert authenticate(STORE, "P-99999", "dana-pw") is None


def test_authenticate_refuses_an_inactive_user_even_with_the_right_password():
    assert authenticate(Store(replace(DANA, active=False)), "P-10041", "dana-pw") is None


def test_authenticate_refuses_a_malformed_stored_hash():
    assert authenticate(Store(replace(DANA, password_hash="plain-text")), "P-10041", "plain-text") is None


# --- issue_token / verify_token ---------------------------------------------------


def test_issue_and_verify_round_trip_gives_a_principal():
    user = DANA
    token = issue_token(user, now=NOW, secret=SECRET)
    principal = verify_token(token, now=NOW + timedelta(hours=1), secret=SECRET, store=STORE)
    assert principal == Principal(user_id="P-10041", role=PATIENT, display_name="דנה כהן")


def test_principal_patient_id_and_is_staff_for_a_patient():
    principal = Principal(user_id="P-10041", role=PATIENT, display_name="דנה כהן")
    assert principal.patient_id == "P-10041"
    assert principal.is_staff is False


def test_principal_patient_id_and_is_staff_for_staff():
    principal = Principal(user_id="coordinator_nurse", role=CLINICAL_STAFF, display_name="אחות מתאמת")
    assert principal.patient_id is None
    assert principal.is_staff is True


def test_verify_token_returns_none_for_expired_token():
    user = DANA
    token = issue_token(user, now=NOW, secret=SECRET, ttl=timedelta(hours=8))
    expired_at = NOW + timedelta(hours=8, seconds=1)
    assert verify_token(token, now=expired_at, secret=SECRET, store=STORE) is None


def test_verify_token_at_exact_expiry_is_none():
    user = DANA
    token = issue_token(user, now=NOW, secret=SECRET, ttl=timedelta(hours=8))
    assert verify_token(token, now=NOW + timedelta(hours=8), secret=SECRET, store=STORE) is None


def test_verify_token_returns_none_for_tampered_payload():
    import base64
    import json

    user = DANA
    token = issue_token(user, now=NOW, secret=SECRET)
    payload_b64, sig_b64 = token.split(".")

    def _b64url_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    def _b64url_decode(text: str) -> bytes:
        padding = "=" * (-len(text) % 4)
        return base64.urlsafe_b64decode(text + padding)

    payload = json.loads(_b64url_decode(payload_b64))
    payload["sub"] = "P-20000"
    tampered_payload_b64 = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    tampered_token = f"{tampered_payload_b64}.{sig_b64}"

    assert verify_token(tampered_token, now=NOW, secret=SECRET, store=STORE) is None


def test_verify_token_returns_none_for_wrong_secret():
    user = DANA
    token = issue_token(user, now=NOW, secret=SECRET)
    assert verify_token(token, now=NOW, secret="a-different-secret", store=STORE) is None


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "not-base64!.also-not-base64!"])
def test_verify_token_returns_none_for_garbage_strings(garbage):
    assert verify_token(garbage, now=NOW, secret=SECRET, store=STORE) is None


def test_verify_token_returns_none_for_unknown_user():
    user = User("P-99999", PATIENT, "לא קיים", hash_password("x"))
    token = issue_token(user, now=NOW, secret=SECRET)
    assert verify_token(token, now=NOW, secret=SECRET, store=STORE) is None


def test_verify_token_refuses_a_user_made_inactive_after_the_token_was_issued():
    token = issue_token(DANA, now=NOW, secret=SECRET)
    assert verify_token(token, now=NOW, secret=SECRET, store=Store(replace(DANA, active=False))) is None


def test_verify_token_returns_none_when_role_differs_from_the_users_table():
    import base64
    import hashlib
    import hmac
    import json

    def _b64url_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    payload = {"sub": "P-10041", "role": CLINICAL_STAFF, "exp": int((NOW + timedelta(hours=1)).timestamp())}
    payload_b64 = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(SECRET.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    token = f"{payload_b64}.{_b64url_encode(sig)}"

    assert verify_token(token, now=NOW, secret=SECRET, store=STORE) is None


def test_verify_token_never_raises_on_arbitrary_garbage():
    for garbage in ["", ".", "..", "a" * 1000, "\x00\x01\x02", "null", "{}", "a.b", "a.b.c.d"]:
        assert verify_token(garbage, now=NOW, secret=SECRET, store=STORE) is None


# --- auth_secret / demo_password ---------------------------------------------------


def test_auth_secret_default_vs_override():
    default_secret = auth_secret(env={})
    assert isinstance(default_secret, str) and default_secret
    assert auth_secret(env={"AUTH_SECRET": "custom-secret"}) == "custom-secret"


def test_demo_password_default_vs_override():
    assert demo_password(env={}) == "demo"
    assert demo_password(env={"DEMO_PASSWORD": "custom"}) == "custom"
