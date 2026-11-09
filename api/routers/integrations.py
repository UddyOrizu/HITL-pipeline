"""Integrations console API: source apps, API keys, OAuth clients, webhooks, delivery log and connectors."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import connectors
from api.deps import UserPrincipal, get_db, require_roles
from api.pagination import Page, paginate
from db.models import ConnectorConfig, SourceApp, WebhookDelivery, Workflow, utcnow
from services import audit, netguard, secretbox, webhooks
from services.security import generate_api_key, generate_secret, hash_secret

router = APIRouter(prefix="/v1/integrations", tags=["integrations"])

owner = require_roles("integration_owner")

CONNECTOR_KINDS = set(connectors.KINDS)


def _iso(dt) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def app_out(a: SourceApp) -> dict:
    grace = a.previous_key_expires_at if a.previous_key_expires_at and a.previous_key_expires_at > utcnow() else None
    return {"id": a.id, "name": a.name, "api_key_prefix": a.api_key_prefix, "webhook_url": a.webhook_url,
            "oauth_client_id": a.oauth_client_id, "oauth_enabled": bool(a.oauth_client_id), "active": a.active,
            "previous_key_valid_until": _iso(grace), "created_at": _iso(a.created_at)}


def _check_webhook(url: str | None) -> str | None:
    url = (url or "").strip() or None
    try:
        netguard.check_url(url, resolve=False)
    except netguard.UnsafeTarget as exc:
        raise HTTPException(422, f"webhook_url: {exc}") from None
    return url


class AppIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    webhook_url: str | None = None
    enable_oauth: bool = False


def _new_oauth(app: SourceApp) -> str:
    app.oauth_client_id = app.oauth_client_id or ("por_client_" + generate_secret(12))
    secret = generate_secret()
    app.oauth_client_secret_hash = hash_secret(secret)
    return secret


def create_source_app(db: Session, tenant_id: str, name: str, webhook_url: str | None, enable_oauth: bool, actor: str) -> dict:
    key, prefix, key_hash = generate_api_key()
    secret = generate_secret()
    app = SourceApp(tenant_id=tenant_id, name=name.strip(), api_key_prefix=prefix, api_key_hash=key_hash,
                    webhook_url=_check_webhook(webhook_url), webhook_secret=secret)
    client_secret = _new_oauth(app) if enable_oauth else None
    db.add(app)
    db.flush()
    audit.record(db, tenant_id, actor, "source_app.created", "source_app", app.id, {"name": app.name, "oauth": enable_oauth})
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
    name: str | None = Field(None, min_length=1, max_length=200)
    webhook_url: str | None = None
    active: bool | None = None


@router.patch("/apps/{app_id}")
def update_app(app_id: str, body: AppPatch, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    a = _app(db, p, app_id)
    changes = body.model_dump(exclude_unset=True)
    if "webhook_url" in changes:
        changes["webhook_url"] = _check_webhook(changes["webhook_url"])
    if "name" in changes and changes["name"] is not None:
        changes["name"] = changes["name"].strip()
    for k, v in changes.items():
        setattr(a, k, v)
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "source_app.updated", "source_app", a.id, changes)
    return app_out(a)


class RotateIn(BaseModel):
    # Hours the old key keeps working, so the app can be redeployed with the new one. 0 revokes it at once.
    grace_hours: int = Field(24, ge=0, le=168)


@router.post("/apps/{app_id}/rotate-key")
def rotate_key(app_id: str, body: RotateIn | None = None, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    a = _app(db, p, app_id)
    grace = (body or RotateIn()).grace_hours
    key, prefix, key_hash = generate_api_key()
    if grace:
        a.previous_key_prefix, a.previous_key_hash = a.api_key_prefix, a.api_key_hash
        a.previous_key_expires_at = utcnow() + timedelta(hours=grace)
    else:
        a.previous_key_prefix = a.previous_key_hash = a.previous_key_expires_at = None
    a.api_key_prefix, a.api_key_hash = prefix, key_hash
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "source_app.key_rotated", "source_app", a.id, {"grace_hours": grace})
    return {**app_out(a), "api_key": key}


@router.post("/apps/{app_id}/revoke-previous-key")
def revoke_previous_key(app_id: str, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    """End the grace period now (e.g. once the app is redeployed, or if the old key leaked)."""
    a = _app(db, p, app_id)
    a.previous_key_prefix = a.previous_key_hash = a.previous_key_expires_at = None
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "source_app.previous_key_revoked", "source_app", a.id, {})
    return app_out(a)


@router.post("/apps/{app_id}/rotate-webhook-secret")
def rotate_secret(app_id: str, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    a = _app(db, p, app_id)
    a.webhook_secret = generate_secret()
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "source_app.webhook_secret_rotated", "source_app", a.id, {})
    return {**app_out(a), "webhook_secret": a.webhook_secret}


class OAuthIn(BaseModel):
    enabled: bool


@router.put("/apps/{app_id}/oauth")
def set_oauth(app_id: str, body: OAuthIn, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    """Turn OAuth client credentials on (returns the client secret once) or off (existing tokens expire within
    the hour; new ones are refused)."""
    a = _app(db, p, app_id)
    secret = None
    if body.enabled and not a.oauth_client_id:
        secret = _new_oauth(a)
    elif not body.enabled:
        a.oauth_client_id = None
        a.oauth_client_secret_hash = None
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "source_app.oauth_updated", "source_app", a.id, {"enabled": body.enabled})
    return {**app_out(a), "oauth_client_secret": secret}


@router.post("/apps/{app_id}/rotate-oauth-secret")
def rotate_oauth_secret(app_id: str, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    a = _app(db, p, app_id)
    if not a.oauth_client_id:
        raise HTTPException(409, "OAuth is not enabled for this app")
    secret = _new_oauth(a)
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "source_app.oauth_secret_rotated", "source_app", a.id, {})
    return {**app_out(a), "oauth_client_secret": secret}


def delivery_out(d: WebhookDelivery, names: dict[str, str] | None = None) -> dict:
    return {"id": d.id, "source_app_id": d.source_app_id, "source_app": (names or {}).get(d.source_app_id),
            "submission_id": d.submission_id, "event": d.event,
            "status": d.status, "attempts": d.attempts, "last_status_code": d.last_status_code, "last_error": d.last_error,
            "created_at": _iso(d.created_at),
            "next_attempt_at": _iso(d.next_attempt_at) if d.status == "pending" else None,
            "delivered_at": _iso(d.delivered_at)}


@router.get("/deliveries")
def list_deliveries(response: Response, app_id: str | None = None, status: str | None = None, event: str | None = None,
                    page: Page = Depends(), p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    q = select(WebhookDelivery).where(WebhookDelivery.tenant_id == p.tenant_id)
    if app_id:
        q = q.where(WebhookDelivery.source_app_id == app_id)
    if status:
        q = q.where(WebhookDelivery.status == status)
    if event:
        q = q.where(WebhookDelivery.event == event)
    names = {a.id: a.name for a in db.scalars(select(SourceApp).where(SourceApp.tenant_id == p.tenant_id)).all()}
    rows = paginate(db, q.order_by(WebhookDelivery.created_at.desc()), page, response)
    return [delivery_out(d, names) for d in rows]


@router.post("/deliveries/{delivery_id}/redeliver")
def redeliver(delivery_id: str, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    d = db.get(WebhookDelivery, delivery_id)
    if d is None or d.tenant_id != p.tenant_id:
        raise HTTPException(404, "delivery not found")
    webhooks.redeliver(db, d)
    return delivery_out(d)


# --- Connectors -------------------------------------------------------------------------


class ConnectorIn(BaseModel):
    source_app_id: str
    kind: str
    config: dict = Field(default_factory=dict)
    active: bool = True


class ConnectorPatch(BaseModel):
    config: dict | None = None
    active: bool | None = None


def connector_out(c: ConnectorConfig) -> dict:
    kind = connectors.kind_of(c)
    spec = connectors.KINDS.get(kind, {"secret": set()})
    return {"id": c.id, "source_app_id": c.source_app_id, "kind": kind, "active": c.active,
            "config": secretbox.mask(c.config, spec["secret"]), "polls": connectors.polls(c),
            "last_run_at": _iso(c.last_run_at), "last_error": c.last_error}


NOTIFY = {"assigned", "escalated", "deferred"}
STATUSES = {"approved", "rejected", "changes_requested"}


def _clean(config: dict) -> dict:
    """Leave out cleared optional settings (null or empty), so each connector's defaults apply."""
    return {k: v for k, v in (config or {}).items() if v is not None and v != ""}


def _validate_config(db: Session, tenant_id: str, kind: str, config: dict) -> None:
    spec = connectors.KINDS[kind]
    if config.get("risk_tier") is not None and config["risk_tier"] not in ("low", "medium", "high"):
        raise HTTPException(422, "risk_tier must be low, medium or high")
    if config.get("content_origin") is not None and config["content_origin"] not in ("ai", "human", "mixed"):
        raise HTTPException(422, "content_origin must be ai, human or mixed")
    if config.get("notify") is not None:
        parts = [p.strip() for p in str(config["notify"]).split(",") if p.strip()]
        if not parts or set(parts) - NOTIFY:
            raise HTTPException(422, f"notify must be a comma-separated list of {', '.join(sorted(NOTIFY))}")
    for key in ("transitions", "states"):
        value = config.get(key)
        if value is not None and (not isinstance(value, dict) or set(value) - STATUSES
                                  or not all(isinstance(v, str | int) for v in value.values())):
            raise HTTPException(422, f"{key} must map approved / rejected / changes_requested to a value")
    for key in [k for k in config if k.endswith("_folder")]:
        if not isinstance(config[key], str):
            raise HTTPException(422, f"{key} must be a folder path")
    missing = [k for k in spec["required"] if not str(config.get(k) or "").strip()]
    needs_workflow = spec["polls"] is True or (isinstance(spec["polls"], str) and config.get(spec["polls"]))
    if needs_workflow and not config.get("workflow_id"):
        missing.append("workflow_id")
    if missing:
        raise HTTPException(422, f"missing {kind} settings: {', '.join(dict.fromkeys(missing))}")
    if config.get("workflow_id"):
        wf = db.scalars(select(Workflow).where(Workflow.tenant_id == tenant_id, Workflow.family_id == config["workflow_id"],
                                               Workflow.is_current.is_(True))).first()
        if wf is None:
            raise HTTPException(422, "workflow_id: unknown workflow")
    for key in spec["urls"]:
        value = secretbox.decrypt(config.get(key))
        if value:
            try:
                netguard.check_url(value, resolve=False)
            except netguard.UnsafeTarget as exc:
                raise HTTPException(422, f"{key}: {exc}") from None


@router.get("/connectors")
def list_connectors(p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    return [connector_out(c) for c in db.scalars(select(ConnectorConfig).where(ConnectorConfig.tenant_id == p.tenant_id)
                                                 .order_by(ConnectorConfig.created_at)).all()]


@router.get("/workflows")
def list_workflows(p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    """Current workflows by name, for choosing where connector submissions go."""
    return [{"id": w.family_id, "name": w.name, "source_app_ids": w.source_app_ids}
            for w in db.scalars(select(Workflow).where(Workflow.tenant_id == p.tenant_id, Workflow.is_current.is_(True))
                                .order_by(Workflow.name)).all()]


@router.get("/connector-kinds")
def connector_kinds(p: UserPrincipal = Depends(owner)):
    """Required, secret and polling settings per connector kind, for building forms."""
    return {k: {"required": v["required"], "secret": sorted(v["secret"]),
                "polls": v["polls"] if isinstance(v["polls"], str) else bool(v["polls"])} for k, v in connectors.KINDS.items()}


@router.post("/connectors", status_code=201)
def create_connector(body: ConnectorIn, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    if body.kind not in CONNECTOR_KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(CONNECTOR_KINDS)}")
    _app(db, p, body.source_app_id)
    config = _clean(body.config)
    _validate_config(db, p.tenant_id, body.kind, config)
    spec = connectors.KINDS[body.kind]
    c = ConnectorConfig(tenant_id=p.tenant_id, source_app_id=body.source_app_id, kind=body.kind,
                        config=secretbox.seal(config, spec["secret"]), active=body.active)
    db.add(c)
    db.flush()
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "connector.created", "connector", c.id,
                 {"kind": c.kind, "config": secretbox.mask(c.config, spec["secret"])})
    return connector_out(c)


def _connector(db: Session, p: UserPrincipal, connector_id: str) -> ConnectorConfig:
    c = db.get(ConnectorConfig, connector_id)
    if c is None or c.tenant_id != p.tenant_id:
        raise HTTPException(404, "connector not found")
    return c


@router.patch("/connectors/{connector_id}")
def update_connector(connector_id: str, body: ConnectorPatch, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    """Change settings or switch a connector on or off. Secret settings sent back as "***" (or left out) keep
    their stored value."""
    c = _connector(db, p, connector_id)
    kind = connectors.kind_of(c)
    spec = connectors.KINDS[kind]
    if body.config is not None:
        merged = secretbox.merge_update(c.config, body.config, spec["secret"])
        _validate_config(db, p.tenant_id, kind, secretbox.reveal(merged))
        c.config = merged
        c.last_error = None
    if body.active is not None:
        c.active = body.active
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "connector.updated", "connector", c.id,
                 {"active": c.active, "config": secretbox.mask(c.config, spec["secret"])})
    return connector_out(c)


@router.delete("/connectors/{connector_id}", status_code=204)
def delete_connector(connector_id: str, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    c = _connector(db, p, connector_id)
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "connector.deleted", "connector", c.id, {"kind": c.kind})
    db.delete(c)


@router.post("/connectors/{connector_id}/run")
def run_connector(connector_id: str, p: UserPrincipal = Depends(owner), db: Session = Depends(get_db)):
    """Run a polling connector now and report the outcome (also a quick way to test the settings)."""
    c = _connector(db, p, connector_id)
    if not connectors.polls(c):
        raise HTTPException(409, "this connector reacts to events and has nothing to run")
    if not c.active:
        raise HTTPException(409, "switch the connector on first")
    db.commit()
    result = connectors.poll_one(c.id, force=True)
    db.expire_all()
    return {**connector_out(db.get(ConnectorConfig, c.id)), "result": result}
