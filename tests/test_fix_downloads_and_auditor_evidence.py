"""Evidence pack download links, and the auditor's evidence record without reviewer identities."""

from urllib.parse import urlsplit

from tests.conftest import auth, make_tenant, review, submit_text


def _released(client):
    world = make_tenant(canary_rate=0.0, spot_check_rate=0.0)
    sub = submit_text(client, world)
    review(client, world, sub["id"])
    return world, sub


def test_evidence_pack_signed_link(client):
    world, sub = _released(client)
    h = {"X-API-Key": world["api_key"]}
    streamed = client.get(f"/v1/submissions/{sub['id']}/evidence", params={"format": "pdf"}, headers=h)
    assert streamed.status_code == 200 and streamed.headers["content-type"] == "application/pdf"

    link = client.get(f"/v1/submissions/{sub['id']}/evidence", params={"format": "pdf", "link": "true"}, headers=h)
    assert link.status_code == 200
    body = link.json()
    assert body["expires_in"] == 300 and body["filename"] == f"evidence-{sub['id']}.pdf"
    # Local storage signs a /v1/files link that works without any credentials.
    url = urlsplit(body["url"])
    fetched = client.get(f"{url.path}?{url.query}")
    assert fetched.status_code == 200 and fetched.content == streamed.content

    auditor = auth(world["users"]["auditor"]["token"])
    a = client.get(f"/v1/audit/submissions/{sub['id']}/evidence.pdf", params={"link": "true"}, headers=auditor)
    assert a.status_code == 200 and "url" in a.json()


def test_auditor_evidence_record_uses_reviewer_pseudonyms(client):
    world, sub = _released(client)
    lead = client.get(f"/v1/audit/submissions/{sub['id']}/evidence", headers=auth(world["users"]["lead"]["token"])).json()
    reviewer_ids = {r["reviewer_id"] for r in lead["reviews"]}
    assert reviewer_ids and not any(rid.startswith("reviewer:") for rid in reviewer_ids)
    assert "redacted" not in lead

    auditor = client.get(f"/v1/audit/submissions/{sub['id']}/evidence", headers=auth(world["users"]["auditor"]["token"]))
    assert auditor.status_code == 200
    record = auditor.json()
    assert record["redacted"] is True
    assert all(r["reviewer_id"].startswith("reviewer:") for r in record["reviews"])
    text = str(record)
    assert not any(rid in text for rid in reviewer_ids)

    # The same reviewer gets the same pseudonym in the audit log.
    events = client.get("/v1/audit/events", headers=auth(world["users"]["auditor"]["token"])).json()
    assert record["reviews"][0]["reviewer_id"] in {e["actor"] for e in events}
