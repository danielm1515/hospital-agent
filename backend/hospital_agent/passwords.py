"""Password hashing for the users table - scrypt from the standard library, no new dependency.

A stored hash is `scrypt$<n>$<r>$<p>$<salt b64>$<key b64>`: the parameters travel with the hash,
so they can be raised later without breaking an existing one. verify_password() never raises -
a malformed stored value is simply not a match - and compares in constant time.

Migration 0009 writes the same format inline (a migration must not import application code
that may change after it ran); tests/test_users.py checks that this module verifies its hashes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

SCHEME = "scrypt"
N, R, P = 2**14, 8, 1  # ~16 MB of memory per hash, within OpenSSL's default 32 MB limit
SALT_BYTES, KEY_BYTES = 16, 32


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else os.urandom(SALT_BYTES)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P, dklen=KEY_BYTES)
    return f"{SCHEME}${N}${R}${P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, key_b64 = stored.split("$")
        if scheme != SCHEME:
            return False
        salt, expected = base64.b64decode(salt_b64, validate=True), base64.b64decode(key_b64, validate=True)
        key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p),
                             dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(key, expected)


# A real hash of nothing anyone can type: authenticate() checks a password against it for an
# unknown or inactive user, so the time a login takes does not tell which user ids exist.
DUMMY_HASH = hash_password(os.urandom(32).hex())
