import json
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from sqlalchemy import select

from app.db import system_session
from app.docs_gen import assets_needing_docs, generate_docs, parse_draft, related_snippets
from app.main import app
from app.models import Asset, AssetColumn, AuditLog, Proposal, User
from app.pii_scan import scan_asset
from app.routers.sources import get_job_queue
from tests.conftest import generate, log_in


class FakeLLM:
    """Answers every draft request with a description and a line per column it was shown."""

    def __init__(self, reply: str | None = None):
        self.prompts = []
        self.reply = reply
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, messages, **request):
        prompt = messages[0]["content"]
        self.prompts.append(prompt)
        columns = [line[2:].split(" (")[0] for line in prompt.splitlines() if line.startswith("- ")]
        reply = self.reply or json.dumps(
            {"description": "Drafted.", "columns": {name: f"About {name}." for name in columns}}
        )
        message = SimpleNamespace(content=reply)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=message)])


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.jobs.append((name, *args))
        return object()


@pytest.fixture
def job_queue():
    queue = FakeJobQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue
    yield queue
    app.dependency_overrides.clear()


def asset_by_path(path: str) -> Asset:
    with system_session() as db:
        return db.scalar(select(Asset).where(Asset.path == path))


def draft(path: str) -> int:
    asset = asset_by_path(path)
    scan_asset(asset.id)
    return generate_docs(asset.id, client=FakeLLM())


def test_every_undocumented_asset_gets_a_draft_once(synced_demo):
    llm = FakeLLM()
    with system_session() as db:
        undocumented = assets_needing_docs(db)
    for asset_id in undocumented:
        scan_asset(asset_id)
        assert generate_docs(asset_id, client=llm) is not None

    with system_session() as db:
        live_assets = db.scalars(select(Asset).where(Asset.archived.is_(False))).all()
        proposals = db.scalars(select(Proposal).where(Proposal.type == "doc")).all()
        assert {p.payload["asset_id"] for p in proposals} == {a.id for a in live_assets}
        assert {a.description_status for a in live_assets} == {"proposed"}
        # Nothing reaches the catalogue before someone approves it.
        assert all(a.description is None for a in live_assets)
        assert assets_needing_docs(db) == []

    # A second run drafts nothing new.
    assert generate_docs(undocumented[0], client=llm) is None


def test_drafts_never_show_the_model_personal_data(synced_demo):
    llm = FakeLLM()
    for path in ["sales_db.public.customers_new", "support_db.public.tickets"]:
        asset = asset_by_path(path)
        scan_asset(asset.id)
        generate_docs(asset.id, client=llm)

    sent = "\n".join(llm.prompts)
    assert "- full_name (text): personal data (PERSON), values withheld" in sent
    with psycopg.connect(make_conninfo(generate.ADMIN_DATABASE_URL, dbname="sales_db")) as sales:
        people = sales.execute("SELECT full_name, phone, email, city FROM customers_new").fetchall()
    for value in {value for row in people for value in row}:
        assert value not in sent


def test_drafts_only_use_slack_notes_every_viewer_can_read(synced_demo):
    with system_session() as db:
        # orders is shared with sales; #data is open to every group, so its note is used.
        orders = related_snippets(db, asset_by_path("sales_db.public.orders"))
        assert any("stopped updating" in text for text in orders)
        # The note about active_customers is in #finance, which sales can't read.
        view = related_snippets(db, asset_by_path("sales_db.public.active_customers"))
        assert not any("90 days" in text for text in view)
        # Notes leave the extension off file names.
        report = related_snippets(db, asset_by_path("sales/sales_report_FINAL_use_this.xlsx"))
        assert any("corrected amounts" in text for text in report)


def test_parse_draft_keeps_only_a_description_and_known_columns():
    reply = 'Thinking... </think> {"description": " Orders. ", "columns": {"a": "A.", "zz": "?"}}'
    assert parse_draft(reply, ["a", "b"]) == {"description": "Orders.", "columns": {"a": "A."}}
    assert parse_draft("no json here", ["a"]) is None
    assert parse_draft('{"description": 5}', ["a"]) is None
    assert parse_draft('{"description": "x", "columns": [1]}', ["a"]) == {
        "description": "x",
        "columns": {},
    }


def test_owners_and_admins_approve_and_descriptions_update(synced_demo, client, job_queue):
    proposal_id = draft("sales_db.public.orders_v2")
    orders_v2 = asset_by_path("sales_db.public.orders_v2")

    # Priya can see the table but doesn't own it, so the proposal isn't hers to review.
    priya = log_in(client, "priya.sharma@chaiwala.co")
    assert client.get("/api/proposals", headers=priya).json() == []
    assert client.post(f"/api/proposals/{proposal_id}/approve", headers=priya).status_code == 404

    with system_session() as db:
        owner_id = db.scalar(select(User.id).where(User.email == "priya.sharma@chaiwala.co"))
        db.get(Asset, orders_v2.id).owner_user_id = owner_id
        db.commit()

    listed = client.get("/api/proposals", headers=priya).json()
    assert [p["id"] for p in listed] == [proposal_id]
    assert listed[0]["description"] == "Drafted."
    assert {"name": "amount", "data_type": "bigint", "description": "About amount."} in listed[0][
        "columns"
    ]

    approved = client.post(f"/api/proposals/{proposal_id}/approve", headers=priya)
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert client.post(f"/api/proposals/{proposal_id}/approve", headers=priya).status_code == 409

    with system_session() as db:
        asset = db.get(Asset, orders_v2.id)
        assert (asset.description, asset.description_status) == ("Drafted.", "approved")
        amount = db.scalar(
            select(AssetColumn).where(
                AssetColumn.asset_id == asset.id, AssetColumn.name == "amount"
            )
        )
        assert (amount.description, amount.description_status) == ("About amount.", "approved")
        assert db.scalar(select(AuditLog.action).where(AuditLog.user_id == owner_id)) == (
            "approve_proposal"
        )
    assert ("embed_missing_job",) in job_queue.jobs

    # The catalogue shows the approved text.
    detail = client.get(f"/api/catalog/assets/{orders_v2.id}", headers=priya).json()
    assert detail["description"] == "Drafted."


def test_a_rejected_draft_changes_nothing_and_is_not_redrafted(synced_demo, client):
    proposal_id = draft("sales_db.public.orders")
    admin = log_in(client, "admin@chaiwala.co")

    rejected = client.post(f"/api/proposals/{proposal_id}/reject", headers=admin)
    assert rejected.json()["status"] == "rejected"

    orders = asset_by_path("sales_db.public.orders")
    assert (orders.description, orders.description_status) == (None, "none")
    with system_session() as db:
        assert orders.id not in assets_needing_docs(db)
