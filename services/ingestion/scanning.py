"""Virus scanning (ClamAV when configured, otherwise an EICAR signature check) and type detection."""

from __future__ import annotations

import socket
import struct

from services.config import get_settings

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class VirusFound(ValueError):
    pass


def scan(data: bytes) -> None:
    s = get_settings()
    if s.clamav_host:
        _clamd_instream(s.clamav_host, s.clamav_port, data)
        return
    if EICAR in data:
        raise VirusFound("Eicar-Test-Signature")


def _clamd_instream(host: str, port: int, data: bytes) -> None:
    with socket.create_connection((host, port), timeout=30) as sock:
        sock.sendall(b"zINSTREAM\0")
        for i in range(0, len(data), 64 * 1024):
            chunk = data[i : i + 64 * 1024]
            sock.sendall(struct.pack("!L", len(chunk)) + chunk)
        sock.sendall(struct.pack("!L", 0))
        reply = sock.recv(4096).decode(errors="replace").strip("\0\n ")
    if "FOUND" in reply:
        raise VirusFound(reply.split(":", 1)[-1].replace("FOUND", "").strip())


# Extension -> canonical format. Magic bytes are checked so a renamed file is rejected.
FORMATS = {
    ".txt": "txt",
    ".docx": "docx",
    ".pptx": "pptx",
    ".ppt": "ppt",
    ".pdf": "pdf",
}

MIME = {
    "txt": "text/plain",
    "text": "text/plain",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppt": "application/vnd.ms-powerpoint",
    "pdf": "application/pdf",
}


class UnsupportedType(ValueError):
    pass


def detect_format(filename: str, data: bytes) -> str:
    name = filename.lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    fmt = FORMATS.get(ext)
    if fmt is None:
        raise UnsupportedType(f"unsupported file type '{ext or filename}'. Supported: .txt, .docx, .pptx, .ppt, .pdf")
    if fmt in ("docx", "pptx") and not data.startswith(b"PK"):
        raise UnsupportedType(f"file is not a valid {ext} (bad signature)")
    if fmt == "pdf" and b"%PDF" not in data[:1024]:
        raise UnsupportedType("file is not a valid .pdf (bad signature)")
    if fmt == "ppt" and not data.startswith(b"\xd0\xcf\x11\xe0"):
        raise UnsupportedType("file is not a valid .ppt (bad signature)")
    if fmt == "txt" and b"\x00" in data[:4096]:
        raise UnsupportedType("file is not plain text")
    return fmt
