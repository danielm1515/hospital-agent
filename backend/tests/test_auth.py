"""The demo IdP: fixed users and HMAC tokens (spec §18.3, design §3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hospital_agent.auth import (
    ADMIN_STAFF,
    CLINICAL_STAFF,
    DEMO_USERS,
    PATIENT,
    STAFF_ROLES,
    DemoUser,
    Principal,
    auth_secret,
    authenticate,
    demo_password,
    issue_token,
    verify_token,
)
from hospital_agent.guards import REVIEWER_ROLES

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
SECRET = "test-secret"


def test_the_five_demo_users_and_their_roles():
    assert set(DEMO_USERS) == {"P-10041", "P-20000", "P-30000", "coordinator_nurse", "admin_coordinator"}

    assert DEMO_USERS["P-10041"] == DemoUser("P-10041", PATIENT, "דנה כהן", identity_verified=True)
    assert DEMO_USERS["P-20000"] == DemoUser("P-20000", PATIENT, "יוסי לוי", identity_verified=True)
    assert DEMO_USERS["P-30000"] == DemoUser("P-30000", PATIENT, "מיכל אברהם", identity_verified=False)
    assert DEMO_USERS["coordinator_nurse"] == DemoUser("coordinator_nurse", CLINICAL_STAFF, "אחות מתאמת")
    assert DEMO_USERS["admin_coordinator"] == DemoUser("admin_coordinator", ADMIN_STAFF, "רכזת מנהלה")

    for user in DEMO_USERS.values():
        assert user.role in {PATIENT, CLINICAL_STAFF, ADMIN_STAFF}


def test_staff_roles_matches_guards_reviewer_roles():
    assert STAFF_ROLES == REVIEWER_ROLES
    assert STAFF_ROLES == frozenset({CLINICAL_STAFF, ADMIN_STAFF})


# --- authenticate -----------------------------------------------------------------


def test_authenticate_right_password_returns_the_user():
    env = {"DEMO_PASSWORD": "demo"}
    user = authenticate("P-10041", "demo", env=env)
    assert user == DEMO_USERS["P-10041"]


def test_authenticate_wrong_password_returns_none():
    env = {"DEMO_PASSWORD": "demo"}
    assert authenticate("P-10041", "wrong", env=env) is None


def test_authenticate_unknown_user_returns_none():
    env = {"DEMO_PASSWORD": "demo"}
    assert authenticate("P-99999", "demo", env=env) is None


def test_authenticate_uses_demo_password_env_override():
    env = {"DEMO_PASSWORD": "sekret"}
    assert authenticate("P-10041", "demo", env=env) is None
    assert authenticate("P-10041", "sekret", env=env) == DEMO_USERS["P-10041"]


def test_authenticate_default_password_is_demo_without_env_override():
    assert authenticate("P-10041", "demo", env={}) == DEMO_USERS["P-10041"]


# --- issue_token / verify_token ---------------------------------------------------


def test_issue_and_verify_round_trip_gives_a_principal():
    user = DEMO_USERS["P-10041"]
    token = issue_token(user, now=NOW, secret=SECRET)
    principal = verify_token(token, now=NOW + timedelta(hours=1), secret=SECRET)
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
    user = DEMO_USERS["P-10041"]
    token = issue_token(user, now=NOW, secret=SECRET, ttl=timedelta(hours=8))
    expired_at = NOW + timedelta(hours=8, seconds=1)
    assert verify_token(token, now=expired_at, secret=SECRET) is None


def test_verify_token_at_exact_expiry_is_none():
    user = DEMO_USERS["P-10041"]
    token = issue_token(user, now=NOW, secret=SECRET, ttl=timedelta(hours=8))
    assert verify_token(token, now=NOW + timedelta(hours=8), secret=SECRET) is None


def test_verify_token_returns_none_for_tampered_payload():
    import base64
    import json

    user = DEMO_USERS["P-10041"]
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

    assert verify_token(tampered_token, now=NOW, secret=SECRET) is None


def test_verify_token_returns_none_for_wrong_secret():
    user = DEMO_USERS["P-10041"]
    token = issue_token(user, now=NOW, secret=SECRET)
    assert verify_token(token, now=NOW, secret="a-different-secret") is None


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "not-base64!.also-not-base64!"])
def test_verify_token_returns_none_for_garbage_strings(garbage):
    assert verify_token(garbage, now=NOW, secret=SECRET) is None


def test_verify_token_returns_none_for_unknown_user():
    user = DemoUser("P-99999", PATIENT, "לא קיים")
    token = issue_token(user, now=NOW, secret=SECRET)
    assert verify_token(token, now=NOW, secret=SECRET) is None


def test_verify_token_returns_none_when_role_differs_from_demo_users():
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

    assert verify_token(token, now=NOW, secret=SECRET) is None


def test_verify_token_never_raises_on_arbitrary_garbage():
    for garbage in ["", ".", "..", "a" * 1000, "\x00\x01\x02", "null", "{}", "a.b", "a.b.c.d"]:
        assert verify_token(garbage, now=NOW, secret=SECRET) is None


# --- auth_secret / demo_password ---------------------------------------------------


def test_auth_secret_default_vs_override():
    default_secret = auth_secret(env={})
    assert isinstance(default_secret, str) and default_secret
    assert auth_secret(env={"AUTH_SECRET": "custom-secret"}) == "custom-secret"


def test_demo_password_default_vs_override():
    assert demo_password(env={}) == "demo"
    assert demo_password(env={"DEMO_PASSWORD": "custom"}) == "custom"
