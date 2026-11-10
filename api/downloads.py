"""Serving stored files (evidence packs) to API clients.

By default the file is streamed through the API, so it works for every client and keeps the caller's
authentication. With ?link=true the caller gets a short-lived signed URL instead (S3/MinIO presigned URL, Azure
user-delegation SAS, or a signed /v1/files link for local storage), for clients that hand the download to a
browser or another system.
"""

from __future__ import annotations

from fastapi import Response

from services.storage import get_storage

LINK_TTL_SECONDS = 300


def stored_file(uri: str, filename: str, media_type: str, link: bool = False):
    storage = get_storage()
    if link:
        return {"url": storage.signed_url(uri, LINK_TTL_SECONDS), "expires_in": LINK_TTL_SECONDS, "filename": filename}
    return Response(storage.get(uri), media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
