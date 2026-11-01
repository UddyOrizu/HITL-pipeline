"""LibreOffice conversion worker: POST /convert?to=pptx with a multipart 'file'. Used for legacy .ppt."""

import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import Response

app = FastAPI(title="Proof of Review LibreOffice worker")
ALLOWED = {"pptx", "docx", "pdf"}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/convert")
async def convert(file: UploadFile, to: str = Query("pptx")):
    if to not in ALLOWED:
        raise HTTPException(400, f"unsupported target {to}")
    suffix = Path(file.filename or "input.ppt").suffix or ".ppt"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{suffix}"
        src.write_bytes(await file.read())
        result = subprocess.run(
            ["soffice", "--headless", "--norestore", f"-env:UserInstallation=file://{tmp}/profile",
             "--convert-to", to, "--outdir", tmp, str(src)],
            capture_output=True, timeout=120,
        )
        out = Path(tmp) / f"input.{to}"
        if result.returncode != 0 or not out.exists():
            raise HTTPException(422, f"conversion failed: {result.stderr.decode(errors='replace')[:300]}")
        return Response(out.read_bytes(), media_type="application/octet-stream")
