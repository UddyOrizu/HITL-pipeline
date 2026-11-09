"""API keys, OAuth client credentials, user tokens and short-lived signed tokens.

Times in tokens are real Unix time (time.time()). Database datetimes are naive UTC, so compare them with
`epoch(dt)`, never `dt.timestamp()` (which would read a naive datetime as local time).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from datetime import UTC, datetime

import jwt

from services.config import get_settings

API_KEY_PREFIX = "por_"
ISSUER = "proof-of-review"


def epoch(dt: datetime) -> int:
    """Unix seconds for a naive-UTC datetime from the database."""
    return int(dt.replace(tzinfo=UTC).timestamp())


def hash_secret(value: str) -> str:
    # Keys are 256-bit random values, so a keyed SHA-256 is sufficient (no need for a slow KDF).
    return hmac.new(get_settings().secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, hash). Only the hash is stored."""
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key[:12], hash_secret(key)


def generate_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def issue_token(subject: str, tenant_id: str, kind: str, roles: list[str] | None = None, ttl_minutes: int | None = None,
                auth_time: int | None = None) -> str:
    """kind: user | app. User tokens carry a jti (so one session can be revoked) and auth_time (when the person
    actually signed in, which caps how long a session can keep refreshing)."""
    s = get_settings()
    now = int(time.time())
    claims = {
        "sub": subject,
        "tid": tenant_id,
        "typ": kind,
        "roles": roles or [],
        "iat": now,
        "exp": now + 60 * (ttl_minutes or s.token_ttl_minutes),
        "iss": ISSUER,
        "jti": uuid.uuid4().hex,
    }
    if kind == "user":
        claims["auth_time"] = auth_time or now
    return jwt.encode(claims, s.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"], issuer=ISSUER)


def sign_payload(kind: str, payload: dict, ttl_seconds: int) -> str:
    """A short-lived signed token for flows such as SSO state. `kind` keeps one kind from being replayed as another."""
    now = int(time.time())
    return jwt.encode({**payload, "typ": kind, "iat": now, "exp": now + ttl_seconds, "iss": ISSUER},
                      get_settings().secret_key, algorithm="HS256")


def read_payload(kind: str, token: str) -> dict:
    claims = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"], issuer=ISSUER)
    if claims.get("typ") != kind:
        raise jwt.InvalidTokenError("wrong token type")
    return claims
