"""Seed a demo tenant: three reviewers, two workflows, a source app and sample files in every format.

Usage: python -m scripts.seed [--submit]
  --submit  also submits each sample file through the API pipeline so queues have work in them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from db.models import Base, SourceApp, Team, Tenant, User, Workflow, new_id
from db.session import get_engine, session_scope
from scripts import sample_files
from services import audit
from services.security import generate_api_key, generate_secret

DEMO_TENANT = "Demo Advisory LLP"
OUT = Path("var/seed")


def seed(create_tables: bool = True) -> dict:
    if create_tables:
        Base.metadata.create_all(get_engine())
    with session_scope() as db:
        existing = db.scalars(select(Tenant).where(Tenant.name == DEMO_TENANT)).first()
        if existing:
            print(f"Demo tenant already exists: {existing.id}")
            info = json.loads((OUT / "demo.json").read_text()) if (OUT / "demo.json").exists() else {"tenant_id": existing.id}
            return info
        tenant = Tenant(name=DEMO_TENANT, region="uk")
        db.add(tenant)
        db.flush()
        t = tenant.id
        team = Team(tenant_id=t, name="Client communications", daily_ceiling_minutes=None)
        db.add(team)
        db.flush()
        people = [
            ("ada@demo.test", "Ada Reviewer", ["reviewer"]),
            ("ben@demo.test", "Ben Reviewer", ["reviewer"]),
            ("cara@demo.test", "Cara Reviewer", ["reviewer"]),
            ("sam@demo.test", "Sam Submitter", ["submitter"]),
            ("olu@demo.test", "Olu Oversight", ["oversight_lead"]),
            ("aud@demo.test", "Ari Auditor", ["auditor"]),
            ("ian@demo.test", "Ina Integrations", ["integration_owner"]),
            ("admin@demo.test", "Demo Admin", ["admin"]),
        ]
        for email, name, roles in people:
            db.add(User(tenant_id=t, email=email, name=name, roles=roles, team_ids=[team.id]))
        key, prefix, key_hash = generate_api_key()
        secret = generate_secret()
        app = SourceApp(tenant_id=t, name="Demo drafting assistant", api_key_prefix=prefix, api_key_hash=key_hash,
                        webhook_url=None, webhook_secret=secret)
        db.add(app)
        common = dict(
            telemetry_policy={"min_reading_ratio": 0.3, "min_coverage": 0.6, "action": "warn"},
            cap_policy={"daily_minutes": 240, "hourly_minutes": 90, "overflow": "queue", "max_consecutive_same_source": 3},
            risk_policy={"mandatory_severities": ["high"]},
        )
        single = Workflow(
            tenant_id=t, family_id=new_id(), version=1, name="Client letters (single review)",
            stages=[{"name": "Review", "team_id": team.id, "sla_hours": 24}], canary_rate=0.2, canary_domain="financial",
            spot_check_rate=0.1, assurance_policy={"min_score": 60, "below_action": "second_review"}, **common,
        )
        two_stage = Workflow(
            tenant_id=t, family_id=new_id(), version=1, name="Advisory reports (two-stage)",
            stages=[{"name": "Preparer review", "team_id": team.id, "sla_hours": 24},
                    {"name": "Partner review", "team_id": team.id, "sla_hours": 48}],
            canary_rate=0.2, canary_domain="professional_services", spot_check_rate=0.1,
            assurance_policy={"min_score": 65, "below_action": "block"}, **common,
        )
        db.add_all([single, two_stage])
        db.flush()
        audit.record(db, t, "system:seed", "tenant.seeded", "tenant", t, {"users": len(people), "workflows": 2})
        info = {
            "tenant_id": t, "source_app_id": app.id, "api_key": key, "webhook_secret": secret,
            "workflows": {"single": single.family_id, "two_stage": two_stage.family_id},
            "users": [p[0] for p in people],
        }

    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in sample_files.ALL.items():
        (OUT / name).write_bytes(fn())
    (OUT / "demo.json").write_text(json.dumps(info, indent=2))
    return info


def submit_samples(info: dict) -> None:
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app)
    headers = {"X-API-Key": info["api_key"]}
    for i, name in enumerate(sample_files.ALL):
        wf = info["workflows"]["two_stage" if name.endswith(".pdf") else "single"]
        with open(OUT / name, "rb") as fh:
            r = client.post("/v1/submissions", headers=headers, files={"file": (name, fh.read())},
                            data={"workflow_id": wf, "external_ref": f"DEMO-{i + 1}", "risk_tier": "medium",
                                  "ai_model": "demo-model", "metadata": json.dumps({"title": name})})
        print(name, r.status_code, r.json().get("status"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    info = seed()
    print(json.dumps({k: v for k, v in info.items() if k != "webhook_secret"}, indent=2))
    print(f"Sample files written to {OUT.resolve()}")
    print("Sign in to the reviewer app with any seeded email, e.g. ada@demo.test (dev login).")
    if args.submit and "api_key" in info:
        submit_samples(info)


if __name__ == "__main__":
    main()
