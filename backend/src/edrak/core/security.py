"""Password hashing and session tokens.

Passwords are hashed with argon2id. Accounts migrated from the v1 database carry their old
unsalted SHA-256 hash as "sha256$<hex>"; those are accepted once and replaced with argon2 at the
next successful sign-in (see needs_rehash).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

LEGACY_PREFIX = "sha256$"
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    if not stored:
        return False
    if stored.startswith(LEGACY_PREFIX):
        legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(stored[len(LEGACY_PREFIX):], legacy)
    try:
        return _hasher.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    if stored.startswith(LEGACY_PREFIX):
        return True
    try:
        return _hasher.check_needs_rehash(stored)
    except InvalidHashError:
        return True


def legacy_sha256(hex_digest: str) -> str:
    """Wrap an unsalted v1 SHA-256 digest so verify_password recognises it."""
    return LEGACY_PREFIX + hex_digest


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Sessions are stored by this digest, so a leaked database does not leak live cookies."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
