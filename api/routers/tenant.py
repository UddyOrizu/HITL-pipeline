"""Organisation settings for admins: name, SSO, retention, cost rates and model prices, notifications, and
removing content on request."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import ROLES, UserPrincipal, get_db, require_roles
from db.models import Submission, Tenant
from db.session import session_scope
from services import audit, costs, netguard, retention, secretbox
from services.sso import allowed_redirects as sso_redirects

router = APIRouter(prefix="/v1/admin", tags=["organisation"])

admin = require_roles("admin")

DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
NOTIFICATIONS = ("assigned", "escalated", "decided")


class PriceIn(BaseModel):
    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float | None = Field(None, ge=0)
    cache_write: float | None = Field(None, ge=0)


class SsoIn(BaseModel):
    enabled: bool = True
    issuer: str | None = None
    client_id: str | None = None
    client_secret: str | None = None  # write-only; "***" or omitted keeps the stored one
    domains: list[str] = Field(default_factory=list)
    auto_provision_roles: list[str] = Field(default_factory=list)
    scopes: str | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    jwks_uri: str | None = None


class TenantSettingsIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    allow_admin_reviewers: bool | None = None
    retention_days: int | None = Field(None, ge=0)  # 0 or null: keep forever
    currency: str | None = Field(None, pattern="^[A-Z]{3}$")
    usd_to_currency: float | None = Field(None, gt=0)
    default_hourly_rate: float | None = Field(None, ge=0)
    model_prices: dict[str, PriceIn] | None = None
    notifications: dict[str, bool] | None = None
    sso: SsoIn | None = None


def settings_out(t: Tenant) -> dict:
    st = dict(t.settings or {})
    sso_cfg = dict(st.get("sso") or {})
    if sso_cfg.get("client_secret"):
        sso_cfg["client_secret"] = secretbox.MASK
    return {
        "id": t.id, "name": t.name, "region": t.region, "active": t.active,
        "allow_admin_reviewers": bool(st.get("allow_admin_reviewers")),
        "retention_days": st.get("retention_days"),
        "currency": st.get("currency") or "USD",
        "usd_to_currency": st.get("usd_to_currency") or 1.0,
        "default_hourly_rate": st.get("default_hourly_rate"),
        "model_prices": st.get("model_prices") or {},
        "default_model_prices": costs.DEFAULT_MODEL_PRICES, "prices_as_of": costs.PRICES_AS_OF,
        "notifications": {k: bool((st.get("notifications") or {}).get(k)) for k in NOTIFICATIONS},
        "sso": sso_cfg or None,
        # Register this with the identity provider. It must be where people open the web app (POR_APP_BASE_URL).
        "sso_redirect_uri": sorted(sso_redirects())[0] if sso_redirects() else None,
        "sso_redirect_uris": sorted(sso_redirects()),
    }


@router.get("/tenant")
def get_tenant(p: UserPrincipal = Depends(admin), db: Session = Depends(get_db)):
    return settings_out(db.get(Tenant, p.tenant_id))


def _check_sso(tenant_id: str, cfg: SsoIn, existing: dict) -> dict:
    if not cfg.issuer or not cfg.client_id:
        raise HTTPException(422, "sso.issuer and sso.client_id are required")
    for key in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        url = getattr(cfg, key)
        if url:
            try:
                netguard.check_url(url, resolve=False)
            except netguard.UnsafeTarget as exc:
                raise HTTPException(422, f"sso.{key}: {exc}") from None
    domains = sorted({d.strip().lower() for d in cfg.domains if d.strip()})
    bad = [d for d in domains if not DOMAIN.match(d)]
    if bad:
        raise HTTPException(422, f"sso.domains: not valid domain names: {bad}")
    roles = [r for r in cfg.auto_provision_roles if r]
    if set(roles) - (ROLES - {"admin"}):
        raise HTTPException(422, "sso.auto_provision_roles may only contain non-admin roles")
    if domains:
        # A domain routes sign-ins to one organisation, so it can belong to only one.
        with session_scope("*") as s:
            for other in s.scalars(select(Tenant).where(Tenant.id != tenant_id)).all():
                taken = set(((other.settings or {}).get("sso") or {}).get("domains") or []) & set(domains)
                if taken:
                    raise HTTPException(409, f"sso.domains already used by another organisation: {sorted(taken)}")
    out = {k: v for k, v in cfg.model_dump().items() if v not in (None, "", [])}
    out["enabled"] = cfg.enabled
    out["domains"] = domains
    out["auto_provision_roles"] = roles
    secret = cfg.client_secret
    if secret in (None, secretbox.MASK):
        if existing.get("client_secret"):
            out["client_secret"] = existing["client_secret"]
        else:
            out.pop("client_secret", None)
    else:
        out["client_secret"] = secretbox.encrypt(secret)
    return out


@router.put("/tenant")
def update_tenant(body: TenantSettingsIn, p: UserPrincipal = Depends(admin), db: Session = Depends(get_db)):
    t = db.get(Tenant, p.tenant_id)
    st = dict(t.settings or {})
    data = body.model_dump(exclude_unset=True)
    changed: list[str] = []
    if "name" in data and data["name"]:
        t.name = data["name"].strip()
        changed.append("name")
    for key in ("allow_admin_reviewers", "currency", "usd_to_currency", "default_hourly_rate"):
        if key in data:
            st[key] = data[key]
            changed.append(key)
    if "retention_days" in data:
        days = data["retention_days"] or None
        if days is not None and days < retention.MIN_RETENTION_DAYS:
            raise HTTPException(422, f"retention_days must be at least {retention.MIN_RETENTION_DAYS} (or empty to keep forever)")
        st["retention_days"] = days
        changed.append("retention_days")
    if body.model_prices is not None:
        st["model_prices"] = {k.strip(): v.model_dump(exclude_none=True) for k, v in body.model_prices.items() if k.strip()}
        changed.append("model_prices")
    if body.notifications is not None:
        unknown = set(body.notifications) - set(NOTIFICATIONS)
        if unknown:
            raise HTTPException(422, f"unknown notifications {sorted(unknown)}")
        st["notifications"] = {k: bool(body.notifications.get(k)) for k in NOTIFICATIONS}
        changed.append("notifications")
    if "sso" in data:
        st["sso"] = _check_sso(t.id, body.sso, st.get("sso") or {}) if body.sso is not None else None
        changed.append("sso")
    if (st.get("currency") or "USD") != "USD" and not st.get("usd_to_currency"):
        raise HTTPException(422, "set usd_to_currency so model spend (priced in USD) can be shown in your currency")
    t.settings = st
    out = settings_out(t)
    audit.record(db, t.id, f"user:{p.user.id}", "tenant.settings_updated", "tenant", t.id,
                 {"changed": changed, "settings": {k: out[k] for k in changed if k in out}})
    return out


@router.delete("/submissions/{submission_id}/content")
def purge_submission(submission_id: str, p: UserPrincipal = Depends(admin), db: Session = Depends(get_db)):
    """Remove a finished submission's content now (e.g. a client's erasure request). Decisions, scores, the audit
    log and the evidence pack are kept."""
    sub = db.get(Submission, submission_id)
    if sub is None or sub.tenant_id != p.tenant_id:
        raise HTTPException(404, "submission not found")
    try:
        result = retention.purge_submission(db, sub, f"user:{p.user.id}", "admin_request")
    except retention.RetentionError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"submission_id": sub.id, "purged_at": sub.purged_at.isoformat() + "Z", **result}
