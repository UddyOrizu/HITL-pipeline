"""Legacy .ppt to .pptx conversion with LibreOffice (HTTP worker or a local soffice binary)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from services.config import get_settings


class ConversionUnavailable(RuntimeError):
    pass


def convert_ppt_to_pptx(data: bytes) -> bytes:
    s = get_settings()
    if s.libreoffice_url:
        resp = httpx.post(
            f"{s.libreoffice_url.rstrip('/')}/convert",
            params={"to": "pptx"},
            files={"file": ("input.ppt", data, "application/vnd.ms-powerpoint")},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.content
    binary = shutil.which(s.soffice_path)
    if not binary:
        raise ConversionUnavailable(
            ".ppt needs LibreOffice: set POR_LIBREOFFICE_URL to the worker or install soffice"
        )
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.ppt"
        src.write_bytes(data)
        subprocess.run(
            [binary, "--headless", "--convert-to", "pptx", "--outdir", tmp, str(src)],
            check=True,
            timeout=120,
            capture_output=True,
        )
        return (Path(tmp) / "input.pptx").read_bytes()
