"""Proof of Review API gateway."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

import connectors
from api.routers import auditor, auth, integrations, oversight, portal, review, submissions, users
from services.config import get_settings
from services.outbound import CanaryLeakError
from services.storage import get_storage, verify_file_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("por.api")

app = FastAPI(
    title="Proof of Review",
    version="0.1.0",
    description="HITL assurance platform: routes content to human reviewers, measures how it was reviewed, "
    "and returns a decision with a Review Assurance Score and evidence trail.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth, submissions, portal, review, oversight, users, auditor, integrations):
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
        if path and target.is_file() and str(target).startswith(str(_dist)):
            return FileResponse(target)
        return FileResponse(_dist / "index.html")


if get_settings().env == "prod" and get_settings().secret_key.startswith("dev-only-insecure"):
    raise RuntimeError("POR_SECRET_KEY must be set in production")
