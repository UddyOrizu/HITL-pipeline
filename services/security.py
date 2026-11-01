"""API keys, OAuth client credentials, user tokens and OIDC verification."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

import jwt

from db.models import utcnow
from services.config import get_settings

API_KEY_PREFIX = "por_"


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


def issue_token(subject: str, tenant_id: str, kind: str, roles: list[str] | None = None, ttl_minutes: int | None = None) -> str:
    s = get_settings()
    now = utcnow()
    claims = {
        "sub": subject,
        "tid": tenant_id,
        "typ": kind,  # user | app
        "roles": roles or [],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes or s.token_ttl_minutes)).timestamp()),
        "iss": "proof-of-review",
    }
    return jwt.encode(claims, s.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"], issuer="proof-of-review")


def verify_oidc_id_token(id_token: str) -> dict:
    """Verify an ID token from the configured OIDC provider (Entra ID, Google) against its JWKS."""
    s = get_settings()
    if not (s.oidc_issuer and s.oidc_jwks_url and s.oidc_audience):
        raise ValueError("OIDC is not configured")
    signing_key = jwt.PyJWKClient(s.oidc_jwks_url).get_signing_key_from_jwt(id_token)
    return jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256", "ES256"],
        audience=s.oidc_audience,
        issuer=s.oidc_issuer,
    )
