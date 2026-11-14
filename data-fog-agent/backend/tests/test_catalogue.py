import shutil
from datetime import date

from sqlalchemy import select

from app import config, embeddings, llm
from app.db import system_session
from app.main import app
from app.models import Asset, AssetPermission, Group, KnowledgeSnippet, Org, Source
from app.routers.sources import get_job_queue
from app.sync import create_source, sync_source
from tests.conftest import log_in

PREFIX = {"postgres": "", "files": "drive:", "slack_export": "slack:"}


def catalogue_with_groups() -> dict:
    """{asset name as used in ground_truth.yaml: sorted group names} for every live asset."""
    with system_session() as db:
        rows = db.execute(
            select(Asset.id, Asset.path, Source.type)
            .join(Source, Source.id == Asset.source_id)
            .where(Asset.archived.is_(False))
        ).all()
        groups = {}
        for asset_id, group_name in db.execute(
            select(AssetPermission.asset_id, Group.name).join(
                Group, Group.id == AssetPermission.group_id
            )
        ):
            groups.setdefault(asset_id, []).append(group_name)
    return {PREFIX[kind] + path: sorted(groups.get(asset_id, [])) for asset_id, path, kind in rows}


def test_full_sync_catalogues_every_demo_asset_with_its_groups(demo, demo_org):
    _, truth = demo
    for source_id in demo_org:
        sync_source(source_id)

    expected = {entry["asset"]: sorted(entry["groups"]) for entry in truth["catalogue"]}
    assert catalogue_with_groups() == expected

    with system_session() as db:
        orders = db.scalar(select(Asset).where(Asset.path == "sales_db.public.orders"))
        sales_2023 = db.scalar(select(Asset).where(Asset.path == "sales_db.public.sales_2023"))
        snippets = db.scalars(select(KnowledgeSnippet.text)).all()
        sources = db.scalars(select(Source)).all()
    # Freshness comes from the newest date in the data, which is what makes these stale.
    assert orders.last_modified_at.date() <= date(2025, 12, 31)
    assert sales_2023.last_modified_at.date() <= date(2023, 12, 31)
    assert orders.row_count > 0
    assert any("orders_v2" in text for text in snippets)
    assert all(source.status == "idle" and source.last_error is None for source in sources)


def test_resync_only_processes_changed_assets(demo, demo_org, tmp_path):
    output_dir, _ = demo
    drive = tmp_path / "drive"
    shutil.copytree(output_dir / "drive", drive)
    with system_session() as db:
        org_id = db.scalar(select(Org.id))
        source = create_source(db, org_id, "files", "drive copy", {"path": str(drive)})
        sales_db = db.scalar(select(Source).where(Source.name == "sales_db"))
        db.commit()
        source_id, sales_db_id = source.id, sales_db.id

    assert len(sync_source(source_id)) == 8
    assert sync_source(source_id) == []
    assert len(sync_source(sales_db_id)) == 8
    assert sync_source(sales_db_id) == []

    export = drive / "sales" / "orders_export_jan.csv"
    export.write_text(export.read_text() + "99999,1,2026-01-31,online,100,completed\n")
    changed = sync_source(source_id)
    with system_session() as db:
        assert [db.get(Asset, asset_id).path for asset_id in changed] == [
            "sales/orders_export_jan.csv"
        ]

    # Deleted from the source: the catalogue keeps the row but marks it archived.
    (drive / "finance" / "targets_2026.xlsx").unlink()
    assert sync_source(source_id) == []
    with system_session() as db:
        targets = db.scalar(select(Asset).where(Asset.path == "finance/targets_2026.xlsx"))
        assert targets.archived


def test_members_only_see_assets_shared_with_their_groups(client, demo_org):
    for source_id in demo_org:
        sync_source(source_id)
    with system_session() as db:
        employee_list_id = db.scalar(select(Asset.id).where(Asset.path == "hr/employee_list.xlsx"))

    sales_user = log_in(client, "priya.sharma@chaiwala.co")
    paths = {
        asset["path"] for asset in client.get("/api/catalog/assets", headers=sales_user).json()
    }
    assert "sales_db.public.orders_v2" in paths
    assert "sales/sales_report_final.xlsx" in paths
    assert "data" in paths
    assert "hr/employee_list.xlsx" not in paths
    assert not any(path.startswith(("finance_db.", "support_db.")) for path in paths)
    response = client.get(f"/api/catalog/assets/{employee_list_id}", headers=sales_user)
    assert response.status_code == 404

    hr_user = log_in(client, "meera.iyer@chaiwala.co")
    detail = client.get(f"/api/catalog/assets/{employee_list_id}", headers=hr_user).json()
    assert detail["groups"] == ["hr"]
    assert "aadhaar" in [column["name"] for column in detail["columns"]]

    found = client.get("/api/catalog/assets", params={"q": "orders_v2"}, headers=sales_user)
    assert [asset["path"] for asset in found.json()] == ["sales_db.public.orders_v2"]


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.jobs.append((name, *args))
        return object()


def test_sources_api_is_admin_only_and_never_returns_credentials(client, demo_org, tmp_path):
    job_queue = FakeJobQueue()
    app.dependency_overrides[get_job_queue] = lambda: job_queue
    try:
        member = log_in(client, "priya.sharma@chaiwala.co")
        assert client.get("/api/sources", headers=member).status_code == 403

        admin = log_in(client, "admin@chaiwala.co")
        sources = client.get("/api/sources", headers=admin)
        assert sources.status_code == 200
        assert len(sources.json()) == 5
        assert not any("config" in source for source in sources.json())
        assert "datafog_reader" not in sources.text

        bad = client.post(
            "/api/sources", headers=admin, json={"type": "files", "name": "x", "config": {}}
        )
        assert bad.status_code == 422

        created = client.post(
            "/api/sources",
            headers=admin,
            json={"type": "files", "name": "more files", "config": {"path": str(tmp_path)}},
        )
        assert created.status_code == 201
        source_id = created.json()["id"]
        assert job_queue.jobs == [("sync_source_job", source_id)]

        assert client.post(f"/api/sources/{source_id}/sync", headers=admin).status_code == 202
        assert client.post("/api/sources/999999/sync", headers=admin).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_embeddings_get_masked_text_and_are_saved(demo_org, monkeypatch):
    slack_id = demo_org[-1]
    sync_source(slack_id)
    with system_session() as db:
        channel = db.scalar(select(Asset).where(Asset.source_id == slack_id))
        db.add(
            KnowledgeSnippet(
                org_id=channel.org_id,
                source_id=slack_id,
                asset_id=channel.id,
                text="Call Priya on 9876543210",
            )
        )
        db.commit()

    sent = []

    def fake_embed_texts(texts):
        sent.extend(texts)
        return [[0.1] * 1536 for _ in texts]

    monkeypatch.setattr(config, "EMBEDDING_API_KEY", "test-key")
    monkeypatch.setattr(llm, "embed_texts", fake_embed_texts)

    assert embeddings.embed_missing() > 0
    assert not any("9876543210" in text for text in sent)
    assert any("XXXX3210" in text for text in sent)
    assert embeddings.embed_missing() == 0
