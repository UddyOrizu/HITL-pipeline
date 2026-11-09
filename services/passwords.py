"""Local passwords for users who sign in without SSO.

Hashes use scrypt from the standard library (memory-hard, no extra dependency). The parameters are stored
in each hash, so they can be raised later without breaking existing passwords.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache

from services.config import get_settings

# OWASP's scrypt baseline: N=2^14 (16 MiB), r=8, p=5.
N, R, P, DKLEN = 2**14, 8, 5, 32
MAX_LENGTH = 256


class PasswordPolicyError(ValueError):
    pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _derive(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=128 * 1024 * 1024)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(_derive(password, salt, N, R, P, DKLEN))}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        want = base64.b64decode(expected)
        got = _derive(password, base64.b64decode(salt), int(n), int(r), int(p), len(want))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


@lru_cache
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(16))


def burn_time(password: str) -> None:
    """Spend the same time as a real check, so response times do not reveal which emails have accounts."""
    verify_password(password, _dummy_hash())


def check_policy(password: str, email: str | None = None) -> None:
    """Raise PasswordPolicyError if the password is not acceptable."""
    minimum = get_settings().password_min_length
    if len(password) < minimum:
        raise PasswordPolicyError(f"password must be at least {minimum} characters")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(f"password must be at most {MAX_LENGTH} characters")
    if len(set(password)) < 4:
        raise PasswordPolicyError("password is too simple")
    if email and password.strip().lower() in {email.lower(), email.split("@")[0].lower()}:
        raise PasswordPolicyError("password must not be your email address")


def set_password(session, user, password: str, actor: str, by: str) -> None:
    """Store a new hash, clear any lockout and end the user's existing sessions. The password is never logged."""
    from db.models import utcnow
    from services import audit

    user.password_hash = hash_password(password)
    user.password_changed_at = utcnow()
    user.failed_logins, user.locked_until = 0, None
    session.flush()
    audit.record(session, user.tenant_id, actor, "user.password_set", "user", user.id, {"by": by})
