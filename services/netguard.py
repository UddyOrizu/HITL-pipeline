"""Outbound URL checks for webhooks and connectors, so a source app or connector cannot point the platform at
internal services (SSRF). In prod only public https targets are allowed unless POR_ALLOW_PRIVATE_TARGETS=true."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from services.config import get_settings


class UnsafeTarget(ValueError):
    pass


def _blocked(ip) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _blocked(ip.ipv4_mapped)
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified)


def check_url(url: str | None, resolve: bool = True) -> None:
    """Raise UnsafeTarget when the URL may not be called. resolve=False skips DNS (at save time, when a name may not
    resolve yet); delivery always resolves."""
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeTarget("URL must be http(s) with a host name")
    if get_settings().private_targets_allowed:
        return
    if parsed.scheme != "https":
        raise UnsafeTarget("URL must use https")
    host = parsed.hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _blocked(literal):
            raise UnsafeTarget("URL points at a private or reserved address")
        return
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeTarget("URL points at a private host name")
    if not resolve:
        return
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeTarget(f"cannot resolve {host}: {exc}") from None
    for info in infos:
        if _blocked(ipaddress.ip_address(info[4][0])):
            raise UnsafeTarget(f"{host} resolves to a private or reserved address")
