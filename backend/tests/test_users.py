"""The users table (migration 0009, docs/spec_corrections.md row 97): the IdP's users, their
role and a password hash each, read by the application only."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from hospital_agent.auth import ADMIN_STAFF, CLINICAL_STAFF, PATIENT, DbUserStore, User, authenticate, demo_password
from hospital_agent.passwords import hash_password, verify_password

SEEDED = {
    "P-10041": (PATIENT, "דנה כהן", True),
    "P-20000": (PATIENT, "יוסי לוי", True),
    "P-30000": (PATIENT, "מיכל אברהם", False),  # the PatientVerificationFailed path, on purpose
    "coordinator_nurse": (CLINICAL_STAFF, "אחות מתאמת", True),
    "admin_coordinator": (ADMIN_STAFF, "רכזת מנהלה", True),
}


def test_the_five_users_their_roles_and_identity_verdicts(app_engine):
    with app_engine.connect() as conn:
        rows = conn.execute(text("SELECT user_id, role, display_name, identity_verified, active FROM users")).all()
    assert {r.user_id: (r.role, r.display_name, r.identity_verified) for r in rows} == SEEDED
    assert all(r.active for r in rows)


def test_every_user_logs_in_with_the_demo_password_and_its_own_hash(app_engine):
    store = DbUserStore(app_engine)
    hashes = set()
    for user_id in SEEDED:
        user = store.get(user_id)
        assert authenticate(store, user_id, demo_password()) == user
        assert authenticate(store, user_id, demo_password() + "x") is None
        hashes.add(user.password_hash)
    assert len(hashes) == len(SEEDED)  # a salt each: no two users share a hash


def test_the_store_answers_none_for_an_unknown_user(app_engine):
    assert DbUserStore(app_engine).get("P-NOBODY") is None


def test_the_database_refuses_a_role_outside_the_three(owner_engine, migrated):
    with pytest.raises(Exception, match="ck_users_role"):
        with owner_engine.begin() as conn:
            conn.execute(text("INSERT INTO users (user_id, role, display_name, password_hash) "
                              "VALUES ('x', 'superuser', 'x', 'x')"))


@pytest.mark.parametrize("statement", [
    "INSERT INTO users (user_id, role, display_name, password_hash) VALUES ('x', 'patient', 'x', 'x')",
    "UPDATE users SET role = 'admin_staff' WHERE user_id = 'P-10041'",
    "UPDATE users SET password_hash = 'x' WHERE user_id = 'P-10041'",
    "DELETE FROM users WHERE user_id = 'P-10041'",
])
def test_the_application_can_read_users_but_never_change_them(app_engine, statement):
    with pytest.raises(ProgrammingError, match="permission denied"):
        with app_engine.begin() as conn:
            conn.execute(text(statement))


def test_an_inactive_user_cannot_log_in_and_its_token_stops_working(app_engine, owner_engine):
    from datetime import UTC, datetime

    from hospital_agent.auth import auth_secret, issue_token, verify_token

    store = DbUserStore(app_engine)
    user = store.get("P-20000")
    token = issue_token(user, now=datetime.now(UTC), secret=auth_secret())
    try:
        with owner_engine.begin() as conn:
            conn.execute(text("UPDATE users SET active = false WHERE user_id = 'P-20000'"))
        assert authenticate(store, "P-20000", demo_password()) is None
        assert verify_token(token, now=datetime.now(UTC), secret=auth_secret(), store=store) is None
    finally:
        with owner_engine.begin() as conn:
            conn.execute(text("UPDATE users SET active = true WHERE user_id = 'P-20000'"))


def test_the_passwords_module_verifies_the_migrations_hashes_and_its_own(app_engine):
    stored = DbUserStore(app_engine).get("P-10041").password_hash
    assert stored.startswith("scrypt$16384$8$1$")
    assert verify_password(demo_password(), stored)
    own = hash_password("something")
    assert verify_password("something", own) and not verify_password("Something", own)


@pytest.mark.parametrize("stored", ["", "plain", "scrypt$1$2$3", "bcrypt$16384$8$1$AAAA$AAAA", "scrypt$x$8$1$AAAA$AAAA"])
def test_a_malformed_stored_hash_is_never_a_match_and_never_raises(stored):
    assert verify_password("anything", stored) is False


def test_the_token_never_carries_the_password_hash():
    """issue_token() writes sub, role and exp only - the hash stays in the table."""
    import json
    from datetime import UTC, datetime

    from hospital_agent.auth import _b64url_decode, issue_token

    token = issue_token(User("P-1", PATIENT, "x", hash_password("pw")), now=datetime.now(UTC), secret="s")
    assert set(json.loads(_b64url_decode(token.split(".")[0]))) == {"sub", "role", "exp"}
