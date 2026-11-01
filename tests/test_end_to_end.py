"""Happy path: submit, review, decide, webhook, evidence."""

from tests.conftest import auth, make_tenant, review, submit_text


def test_text_submission_flows_to_signed_decision(client, sink):
    world = make_tenant()
    sub = submit_text(client, world)
    assert sub["status"] == "in_review"

    _, result = review(client, world, sub["id"], "approve")
    assert result["status"] == "recorded"

    final = client.get(f"/v1/submissions/{sub['id']}", headers={"X-API-Key": world["api_key"]}).json()
    assert final["status"] == "approved"
    assert final["decision"]["outcome"] == "approved"
    assert 0 <= final["decision"]["assurance"]["score"] <= 100

    events = [e["event"] for e in sink.events()]
    assert events[0] == "submission.received"
    assert "submission.in_review" in events
    assert events[-1] == "submission.decided"

    from services.webhooks import verify

    last = sink.requests[-1]
    assert verify(world["webhook_secret"], last.headers["X-PoR-Signature"], last.content)

    ev = client.get(f"/v1/submissions/{sub['id']}/evidence", headers={"X-API-Key": world["api_key"]}).json()
    assert ev["audit_chain"]["valid"] is True
    pdf = client.get(f"/v1/submissions/{sub['id']}/evidence?format=pdf", headers={"X-API-Key": world["api_key"]})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    lead = world["users"]["lead"]["token"]
    dash = client.get("/v1/oversight/dashboard", headers=auth(lead)).json()
    assert dash["volume"]["submissions"] == 1
