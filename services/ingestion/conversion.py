"""Legacy .ppt to .pptx conversion with LibreOffice (HTTP worker or a local soffice binary).

Failures are typed for the pipeline's retry policy: ConversionUnavailable (worker down, 5xx, network error,
timeout) is retried with backoff; ExtractionError (the worker or soffice rejected this file, a 4xx or a failed
conversion) defers the submission straight away, because retrying the same file fails the same way.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from services.config import get_settings
from services.ingestion.extractors import ExtractionError


class ConversionUnavailable(RuntimeError):
    pass


def convert_ppt_to_pptx(data: bytes) -> bytes:
    s = get_settings()
    if s.libreoffice_url:
        try:
            resp = httpx.post(
                f"{s.libreoffice_url.rstrip('/')}/convert",
                params={"to": "pptx"},
                files={"file": ("input.ppt", data, "application/vnd.ms-powerpoint")},
                timeout=120,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if 400 <= code < 500 and code not in (408, 429):
                raise ExtractionError(f"the LibreOffice worker could not convert this .ppt (HTTP {code})") from exc
            raise ConversionUnavailable(f"the LibreOffice worker is unavailable (HTTP {code})") from exc
        except httpx.TransportError as exc:
            raise ConversionUnavailable(f"the LibreOffice worker is unreachable: {exc}") from exc
        return resp.content
    binary = shutil.which(s.soffice_path)
    if not binary:
        raise ConversionUnavailable(
            ".ppt needs LibreOffice: set POR_LIBREOFFICE_URL to the worker or install soffice"
        )
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.ppt"
        src.write_bytes(data)
        try:
            subprocess.run(
                [binary, "--headless", "--convert-to", "pptx", "--outdir", tmp, str(src)],
                check=True,
                timeout=120,
                capture_output=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionUnavailable("LibreOffice timed out converting the .ppt") from exc
        except subprocess.CalledProcessError as exc:
            raise ExtractionError(f"LibreOffice could not convert this .ppt (exit code {exc.returncode})") from exc
        out = Path(tmp) / "input.pptx"
        if not out.exists():
            raise ExtractionError("LibreOffice produced no .pptx for this file")
        return out.read_bytes()
