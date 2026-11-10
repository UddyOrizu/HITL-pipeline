"""Proof of Review API gateway."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

import connectors
from api.routers import (
    auditor,
    auth,
    integrations,
    oversight,
    platform,
    portal,
    reports,
    review,
    submissions,
    tenant,
    users,
)
from services import bootstrap, metrics, scheduler
from services.config import DEFAULT_SECRET_KEY, get_settings
from services.logging_setup import configure as configure_logging
from services.logging_setup import request_id
from services.outbound import CanaryLeakError
from services.security import constant_time_equal
from services.storage import get_storage, verify_file_signature

configure_logging()
log = logging.getLogger("por.api")


def check_production_settings() -> None:
    """Refuse to start in prod with settings that would be unsafe there."""
    s = get_settings()
    if not s.is_prod:
        return
    key = (s.secret_key or "").strip()
    if key == DEFAULT_SECRET_KEY or key.startswith("dev-only-insecure") or len(key) < 32:
        raise RuntimeError("POR_SECRET_KEY must be set in production to a random value of at least 32 characters")
    if not (s.encryption_key or "").strip():
        raise RuntimeError("POR_ENCRYPTION_KEY must be set in production (it encrypts stored connector and SSO "
                           "secrets; without it they are encrypted with a key derived from POR_SECRET_KEY)")
    if s.metrics_enabled and not (s.metrics_token or "").strip():
        raise RuntimeError("POR_METRICS_TOKEN must be set in production while metrics are on (or set "
                           "POR_METRICS_ENABLED=false)")
    if s.virus_scan_required and not s.clamav_host:
        raise RuntimeError("POR_CLAMAV_HOST must be set in production (or POR_REQUIRE_VIRUS_SCAN=false to accept "
                           "uploads without virus scanning)")
    if "*" in s.cors_origin_list:
        raise RuntimeError("POR_CORS_ORIGINS must list origins explicitly in production, not *")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    check_production_settings()
    # Create the initial admin from POR_BOOTSTRAP_ADMIN_* when set. Bad settings stop start-up loudly.
    bootstrap.ensure_initial_admin()
    if get_settings().run_scheduler_in_api:
        scheduler.start_background()
    yield
    scheduler.stop_background()


app = FastAPI(
    lifespan=lifespan,
    title="Proof of Review",
    version="0.2.0",
    description="HITL assurance platform: routes content to human reviewers, measures how it was reviewed, "
    "and returns a decision with a Review Assurance Score and evidence trail.",
)

# Origins come from POR_CORS_ORIGINS. The bundled web app is same-origin (served by the API, or proxied by Vite),
# so the default is none.
_origins = get_settings().cors_origin_list
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Total-Count", "X-Next-Offset", "X-Request-ID", "Retry-After", "Content-Disposition"],
    )


@app.middleware("http")
async def observe(request: Request, call_next):
    """Request ids (X-Request-ID, also in every log line) and request metrics by route template."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    token = request_id.set(rid)
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", None) or "unmatched"
        labels = {"method": request.method, "route": path, "status": str(status)}
        metrics.inc("por_http_requests_total", labels)
        metrics.observe("por_http_request_duration_seconds", time.perf_counter() - start,
                        {"method": request.method, "route": path})
        request_id.reset(token)


for r in (auth, submissions, portal, review, oversight, users, tenant, platform, reports, auditor, integrations):
    app.include_router(r.router)

connectors.register()


@app.exception_handler(CanaryLeakError)
async def _canary_leak(request: Request, exc: CanaryLeakError):
    # Fail closed: never return a payload that would leak review-only content.
    log.error("blocked outbound payload: %s", exc)
    return JSONResponse({"detail": "response blocked by canary leak guard"}, status_code=500)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    """Ready to serve: the database answers."""
    from sqlalchemy import text

    from db.session import session_scope

    try:
        with session_scope() as s:
            s.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": type(exc).__name__}, status_code=503)
    return {"ok": True}


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request):
    s = get_settings()
    if not s.metrics_enabled:
        raise HTTPException(404, "not found")
    if s.metrics_token:
        auth_header = request.headers.get("authorization", "")
        if not constant_time_equal(auth_header, f"Bearer {s.metrics_token}"):
            raise HTTPException(401, "metrics token required")
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")


@app.get("/v1/files/{key:path}")
def signed_file(key: str, exp: int, sig: str):
    """Short-lived signed URLs for locally stored originals and evidence packs."""
    if not verify_file_signature(key, exp, sig):
        raise HTTPException(403, "invalid or expired link")
    data = get_storage().get(f"local://{key}")
    return Response(data, media_type="application/octet-stream")


# Serve the built reviewer app when present (web/dist), so one container can run everything.
_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if _dist.exists():

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("v1/"):
            raise HTTPException(404, "not found")
        target = (_dist / path).resolve()
        if path and target.is_relative_to(_dist) and target.is_file():
            return FileResponse(target)
        return FileResponse(_dist / "index.html")


check_production_settings()
