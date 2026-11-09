"""Encryption for secrets stored in the database (connector credentials, tenant SSO client secrets).

Values are stored as "enc:v1:<fernet token>". The key is POR_ENCRYPTION_KEY, or one derived from POR_SECRET_KEY.
Plain values written before encryption existed are still read as-is and encrypted on the next save.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from services.config import get_settings

PREFIX = "enc:v1:"
MASK = "***"


def _fernet() -> Fernet:
    s = get_settings()
    material = s.encryption_key or s.secret_key
    try:
        if s.encryption_key and len(base64.urlsafe_b64decode(material.encode())) == 32:
            return Fernet(material.encode())
    except (ValueError, TypeError):
        pass
    key = base64.urlsafe_b64encode(hashlib.sha256(("por-secretbox:" + material).encode()).digest())
    return Fernet(key)


def encrypt(value: str | None) -> str | None:
    if value is None or value == "" or str(value).startswith(PREFIX):
        return value
    return PREFIX + _fernet().encrypt(str(value).encode()).decode()


def decrypt(value):
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        raise ValueError("stored secret cannot be decrypted (was POR_ENCRYPTION_KEY or POR_SECRET_KEY changed?)") from None


def seal(config: dict, secret_fields: set[str]) -> dict:
    return {k: (encrypt(v) if k in secret_fields and isinstance(v, str) else v) for k, v in (config or {}).items()}


def reveal(config: dict | None) -> dict:
    return {k: decrypt(v) for k, v in (config or {}).items()}


def mask(config: dict | None, secret_fields: set[str]) -> dict:
    return {k: (MASK if k in secret_fields and v else (v if not (isinstance(v, str) and v.startswith(PREFIX)) else MASK))
            for k, v in (config or {}).items()}


def merge_update(existing: dict, incoming: dict, secret_fields: set[str]) -> dict:
    """Apply an edit: secret fields sent back as the mask (or left out) keep their stored value."""
    out = dict(existing or {})
    for k, v in incoming.items():
        if k in secret_fields and (v == MASK or v is None):
            continue
        if v is None or v == "":
            out.pop(k, None)  # cleared: the connector's default applies
            continue
        out[k] = v
    return seal(out, secret_fields)
