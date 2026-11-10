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


USER_CHANNELS = ("ui", "api", "mcp")


def issue_token(subject: str, tenant_id: str, kind: str, roles: list[str] | None = None, ttl_minutes: int | None = None,
                auth_time: int | None = None, *, chan: str | None = "ui", sid: str | None = None,
                extra: dict | None = None) -> str:
    """kind: user | app.

    User tokens carry:
    - jti: one token can be revoked;
    - auth_time: when the person actually signed in, which caps how long the session can keep refreshing
      (exp is never later than auth_time + POR_SESSION_MAX_HOURS);
    - sid: the session id, kept across refreshes and shared by tokens issued from the session (POST /v1/me/tokens),
      so signing out ends all of them;
    - chan: the client the token was issued for (ui for the web app's sign-in flows, mcp or api for tokens made for
      other clients). Pass chan=None only for tokens that should behave like ones issued before channels existed.
    """
    s = get_settings()
    now = int(time.time())
    exp = now + 60 * (ttl_minutes or s.token_ttl_minutes)
    claims = {
        "sub": subject,
        "tid": tenant_id,
        "typ": kind,
        "roles": roles or [],
        "iat": now,
        "exp": exp,
        "iss": ISSUER,
        "jti": uuid.uuid4().hex,
    }
    if kind == "user":
        at = auth_time or now
        claims["auth_time"] = at
        claims["exp"] = min(exp, session_expiry(at))
        claims["sid"] = sid or uuid.uuid4().hex
        if chan is not None:
            if chan not in USER_CHANNELS:
                raise ValueError(f"unknown channel {chan}")
            claims["chan"] = chan
    if extra:
        claims.update(extra)
    return jwt.encode(claims, s.secret_key, algorithm="HS256")


def session_expiry(auth_time: int) -> int:
    """The latest moment a session that started at auth_time may last (Unix seconds)."""
    return int(auth_time) + int(get_settings().session_max_hours * 3600)


def oauth_fingerprint(secret_hash: str | None) -> str:
    """Short fingerprint of a stored OAuth client secret hash. Tokens from the client credentials grant carry it, so
    rotating the secret (or turning OAuth off) ends the tokens issued with the old one."""
    return hashlib.sha256((secret_hash or "").encode()).hexdigest()[:16]


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
