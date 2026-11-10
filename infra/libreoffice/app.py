"""LibreOffice conversion worker: POST /convert?to=pptx with a multipart 'file'. Used for legacy .ppt.

Conversions run in worker threads, at most LIBREOFFICE_CONCURRENCY at a time (default 2), so /healthz keeps
answering while a conversion runs. Uploads larger than LIBREOFFICE_MAX_UPLOAD_MB (default 50) are refused with 413.
"""

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import Response

app = FastAPI(title="Proof of Review LibreOffice worker")
ALLOWED = {"pptx", "docx", "pdf"}
MAX_UPLOAD_BYTES = int(float(os.environ.get("LIBREOFFICE_MAX_UPLOAD_MB", "50")) * 1024 * 1024)
CONCURRENCY = max(1, int(os.environ.get("LIBREOFFICE_CONCURRENCY", "2")))
TIMEOUT_SECONDS = int(os.environ.get("LIBREOFFICE_TIMEOUT_SECONDS", "120"))
CHUNK = 1024 * 1024

_slots: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _slots
    if _slots is None:
        _slots = asyncio.Semaphore(CONCURRENCY)
    return _slots


@app.middleware("http")
async def refuse_large_bodies(request, call_next):
    """Refuse an oversized upload from its Content-Length, before the multipart parser spools it to disk."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES + CHUNK:  # + room for the multipart framing
        return Response(f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB", status_code=413)
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


async def _read_limited(file: UploadFile) -> bytes:
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    data = bytearray()
    while chunk := await file.read(CHUNK):
        data += chunk
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    return bytes(data)


def _convert(data: bytes, suffix: str, to: str) -> bytes:
    """Blocking: runs soffice in a fresh profile directory."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{suffix}"
        src.write_bytes(data)
        try:
            result = subprocess.run(
                ["soffice", "--headless", "--norestore", f"-env:UserInstallation=file://{tmp}/profile",
                 "--convert-to", to, "--outdir", tmp, str(src)],
                capture_output=True, timeout=TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(504, f"conversion took longer than {TIMEOUT_SECONDS}s") from None
        out = Path(tmp) / f"input.{to}"
        if result.returncode != 0 or not out.exists():
            raise HTTPException(422, f"conversion failed: {result.stderr.decode(errors='replace')[:300]}")
        return out.read_bytes()


@app.post("/convert")
async def convert(file: UploadFile, to: str = Query("pptx")):
    if to not in ALLOWED:
        raise HTTPException(400, f"unsupported target {to}")
    suffix = Path(file.filename or "input.ppt").suffix or ".ppt"
    if not suffix[1:].isalnum():
        suffix = ".ppt"
    data = await _read_limited(file)
    async with _semaphore():
        converted = await asyncio.to_thread(_convert, data, suffix, to)
    return Response(converted, media_type="application/octet-stream")
