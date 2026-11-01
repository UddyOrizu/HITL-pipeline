"""Integrations console API: source apps, API keys, webhooks, delivery log and connectors."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import UserPrincipal, get_db, require_roles
from db.models import ConnectorConfig, SourceApp, WebhookDelivery
from services import audit, webhooks
from services.security import generate_api_key, generate_secret, hash_secret

router = APIRouter(prefix="/v1/integrations", tags=["integrations"])

owner = require_roles("integration_owner")

CONNECTOR_KINDS = {"sharepoint", "teams", "jira"}


def app_out(a: SourceApp) -> dict:
    return {"id": a.id, "name": a.name, "api_key_prefix": a.api_key_prefix, "webhook_url": a.webhook_url,
            "oauth_client_id": a.oauth_client_id, "active": a.active, "created_at": a.created_at.isoformat() + "Z"}


class AppIn(BaseModel):
    name: str
    webhook_url: str | None = None
    enable_oauth: bool = False


def create_source_app(db: Session, tenant_id: str, name: str, webhook_url: str | None, enable_oauth: bool, actor: str) -> dict:
    key, prefix, key_hash = generate_api_key()
    secret = generate_secret()
    app = SourceApp(tenant_id=tenant_id, name=name, api_key_prefix=prefix, api_key_hash=key_hash, webhook_url=webhook_url,
                    webhook_secret=secret)
    client_secret = None
    if enable_oauth:
        app.oauth_client_id = "por_client_" + generate_secret(12)
        client_secret = generate_secret()
        app.oauth_client_secret_hash = hash_secret(client_secret)
    db.add(app)
    db.flush()
    audit.record(db, tenant_id, actor, "source_app.created", "source_app", app.id, {"name": name, "oauth": enable_oauth})
    # Secrets are shown once, at creation.
    return {**app_out(app), "api_key": key, "webhook_secret": secret, "oauth_client_secret": client_secret}


@router.get("/apps")
def list_apps(p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    # The built-in web portal app is managed by the platform, so it is not listed or editable here.
    return [app_out(a) for a in db.scalars(select(SourceApp).where(SourceApp.tenant_id == p.tenant_id, SourceApp.kind == "api")
                                           .order_by(SourceApp.name)).all()]


@router.post("/apps", status_code=201)
def create_app(body: AppIn, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    return create_source_app(db, p.tenant_id, body.name, body.webhook_url, body.enable_oauth, f"user:{p.user.id}")


def _app(db: Session, p: UserPrincipal, app_id: str) -> SourceApp:
    a = db.get(SourceApp, app_id)
    if a is None or a.tenant_id != p.tenant_id or a.kind != "api":
        raise HTTPException(404, "source app not found")
    return a


class AppPatch(BaseModel):
    name: str | None = None
    webhook_url: str | None = None
    active: bool | None = None


@router.patch("/apps/{app_id}")
def update_app(app_id: str, body: AppPatch, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    a = _app(db, p, app_id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(a, k, v)
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "source_app.updated", "source_app", a.id, changes)
    return app_out(a)


@router.post("/apps/{app_id}/rotate-key")
def rotate_key(app_id: str, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    a = _app(db, p, app_id)
    key, prefix, key_hash = generate_api_key()
    a.api_key_prefix, a.api_key_hash = prefix, key_hash
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "source_app.key_rotated", "source_app", a.id, {})
    return {**app_out(a), "api_key": key}


@router.post("/apps/{app_id}/rotate-webhook-secret")
def rotate_secret(app_id: str, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    a = _app(db, p, app_id)
    a.webhook_secret = generate_secret()
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "source_app.webhook_secret_rotated", "source_app", a.id, {})
    return {**app_out(a), "webhook_secret": a.webhook_secret}


def delivery_out(d: WebhookDelivery) -> dict:
    return {"id": d.id, "source_app_id": d.source_app_id, "submission_id": d.submission_id, "event": d.event,
            "status": d.status, "attempts": d.attempts, "last_status_code": d.last_status_code, "last_error": d.last_error,
            "created_at": d.created_at.isoformat() + "Z",
            "next_attempt_at": d.next_attempt_at.isoformat() + "Z" if d.status == "pending" else None,
            "delivered_at": d.delivered_at.isoformat() + "Z" if d.delivered_at else None}


@router.get("/deliveries")
def list_deliveries(app_id: str | None = None, status: str | None = None, limit: int = Query(100, le=500),
                    p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    q = select(WebhookDelivery).where(WebhookDelivery.tenant_id == p.tenant_id)
    if app_id:
        q = q.where(WebhookDelivery.source_app_id == app_id)
    if status:
        q = q.where(WebhookDelivery.status == status)
    return [delivery_out(d) for d in db.scalars(q.order_by(WebhookDelivery.created_at.desc()).limit(limit)).all()]


@router.post("/deliveries/{delivery_id}/redeliver")
def redeliver(delivery_id: str, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    d = db.get(WebhookDelivery, delivery_id)
    if d is None or d.tenant_id != p.tenant_id:
        raise HTTPException(404, "delivery not found")
    webhooks.redeliver(db, d)
    return delivery_out(d)


class ConnectorIn(BaseModel):
    source_app_id: str
    kind: str
    config: dict = Field(default_factory=dict)
    active: bool = True


def connector_out(c: ConnectorConfig) -> dict:
    secret_keys = {"client_secret", "api_token", "password"}
    return {"id": c.id, "source_app_id": c.source_app_id, "kind": c.kind, "active": c.active,
            "config": {k: ("***" if k in secret_keys else v) for k, v in (c.config or {}).items()}}


@router.get("/connectors")
def list_connectors(p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    return [connector_out(c) for c in db.scalars(select(ConnectorConfig).where(ConnectorConfig.tenant_id == p.tenant_id)).all()]


@router.post("/connectors", status_code=201)
def create_connector(body: ConnectorIn, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    if body.kind not in CONNECTOR_KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(CONNECTOR_KINDS)}")
    _app(db, p, body.source_app_id)
    c = ConnectorConfig(tenant_id=p.tenant_id, source_app_id=body.source_app_id, kind=body.kind, config=body.config,
                        active=body.active)
    db.add(c)
    db.flush()
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "connector.created", "connector", c.id, {"kind": c.kind})
    return connector_out(c)
