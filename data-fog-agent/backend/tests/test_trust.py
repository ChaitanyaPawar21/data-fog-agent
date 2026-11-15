from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import agent, trust
from app.db import SessionLocal, system_session
from app.models import Alert, Asset, AuditLog, Org, User
from app.sync import create_source, sync_source
from tests.conftest import log_in


def ids_by_path() -> dict[str, int]:
    with system_session() as db:
        rows = db.execute(select(Asset.path, Asset.id).where(Asset.archived.is_(False))).all()
    return dict(rows)


def use(asset_ids, days_ago: int = 1):
    """Records that the agent used these assets, the way its tools do."""
    with system_session() as db:
        org_id = db.scalar(select(Org.id))
        db.add(
            AuditLog(
                org_id=org_id,
                actor="agent",
                action="get_schema",
                details={"asset_ids": list(asset_ids)},
                created_at=datetime.now(UTC) - timedelta(days=days_ago),
            )
        )
        db.commit()


def refresh() -> int:
    return trust.refresh_all()


def open_alerts(alert_type: str) -> dict[str, str]:
    with system_session() as db:
        rows = db.execute(
            select(Asset.path, Alert.message)
            .join(Asset, Asset.id == Alert.asset_id)
            .where(Alert.type == alert_type, Alert.resolved_at.is_(None))
        ).all()
    return dict(rows)


def catalogue_name(ground_truth_asset: str) -> str:
    return ground_truth_asset.removeprefix("drive:")


def test_stale_assets_that_are_still_used_get_one_alert(demo, synced_demo):
    _, ground_truth = demo
    paths = ids_by_path()
    stale = {catalogue_name(entry["asset"]) for entry in ground_truth["stale_assets"]}

    # Everything was used except sales_2023, which is stale but nobody touches.
    use(asset_id for path, asset_id in paths.items() if path != "sales_db.public.sales_2023")
    use([paths["sales_db.public.sales_2023"]], days_ago=45)  # too long ago to count
    refresh()

    alerted = set(open_alerts("stale"))
    assert stale - {"sales_db.public.sales_2023"} <= alerted
    assert "sales_db.public.sales_2023" not in alerted
    # Modified 20 days ago, so not stale. (Demo tables have fixed dates and will age into
    # staleness over time; files are dated relative to today, so these stay fresh.)
    assert "finance/targets_2026.xlsx" not in alerted
    assert "sales/sales_report_FINAL_use_this.xlsx" not in alerted
    assert "data" not in alerted  # a quiet Slack channel isn't stale data
    assert "hasn't changed in" in open_alerts("stale")["sales_db.public.orders"]

    # Still stale tomorrow: no second alert for the same stale spell.
    assert refresh() == 0

    # The data changes again: the alert closes on its own.
    with system_session() as db:
        db.get(Asset, paths["sales_db.public.orders"]).last_modified_at = datetime.now(UTC)
        db.commit()
    refresh()
    assert "sales_db.public.orders" not in open_alerts("stale")


def test_orders_v2_outranks_orders(synced_demo, client):
    paths = ids_by_path()
    use(paths.values())
    refresh()

    with system_session() as db:
        org_id = db.scalar(select(Org.id))
        older = trust.older_copy_ids(db, org_id)
        score = {p: db.get(Asset, i).trust_score for p, i in paths.items()}

    by_id = {asset_id: path for path, asset_id in paths.items()}
    older_paths = {by_id[i] for i in older}
    assert {
        "sales_db.public.orders",
        "sales_db.public.customers",
        "sales_db.public.customers_backup_2023",
        "sales/sales_report_final.xlsx",
        "sales/sales_report_final_v2.xlsx",
    } <= older_paths
    exports = {"sales/orders_export_jan.csv", "sales/orders_export_jan (1).csv"}
    assert len(older_paths & exports) == 1  # same date: either one is "the copy"
    for current in [
        "sales_db.public.orders_v2",
        "sales_db.public.customers_new",
        "sales/sales_report_FINAL_use_this.xlsx",
    ]:
        assert current not in older_paths

    assert score["sales_db.public.orders_v2"] > score["sales_db.public.orders"]
    assert score["sales_db.public.customers_new"] > score["sales_db.public.customers"]
    assert score["sales_db.public.customers_new"] > score["sales_db.public.customers_backup_2023"]

    # Search ranks by trust, in the catalogue and in the agent's tool.
    priya = log_in(client, "priya.sharma@chaiwala.co")
    found = client.get("/api/catalog/assets", params={"q": "orders"}, headers=priya).json()
    found_paths = [asset["path"] for asset in found]
    assert found_paths.index("sales_db.public.orders_v2") < found_paths.index(
        "sales_db.public.orders"
    )
    trusted = client.get("/api/catalog/assets", params={"min_trust": 40}, headers=priya).json()
    assert "sales_db.public.orders" not in [asset["path"] for asset in trusted]

    with system_session() as db:
        user = db.scalar(select(User).where(User.email == "priya.sharma@chaiwala.co"))
    with SessionLocal(info={"org_id": user.org_id}) as db:
        result, _ = agent.run_tool(db, db.get(User, user.id), "search_catalog", {"query": "orders"})
        listed = [asset["path"] for asset in result["assets"]]
        assert listed.index("sales_db.public.orders_v2") < listed.index("sales_db.public.orders")
        # The tool call is audited with what it returned, which is what "used" counts.
        db.commit()
    with system_session() as db:
        details = db.scalars(
            select(AuditLog.details).where(AuditLog.action == "search_catalog")
        ).all()[-1]
    assert paths["sales_db.public.orders_v2"] in details["asset_ids"]


def test_asset_page_shows_the_points_behind_the_score(synced_demo, client):
    paths = ids_by_path()
    use([paths["sales_db.public.orders_v2"]])
    refresh()
    priya = log_in(client, "priya.sharma@chaiwala.co")
    detail = client.get(
        f"/api/catalog/assets/{paths['sales_db.public.orders_v2']}", headers=priya
    ).json()
    factors = detail["trust_factors"]
    assert factors["recently_used"] == 15
    assert factors["not_a_copy"] == 15
    assert factors["owner"] == 0
    assert sum(factors.values()) == detail["trust_score"]


def test_unknown_freshness_scores_no_points(synced_demo):
    # customers_backup_2023 keeps its dates as text, so its age is unknown, not "today".
    with system_session() as db:
        backup = db.scalar(
            select(Asset).where(Asset.path == "sales_db.public.customers_backup_2023")
        )
        assert backup.last_modified_at is None
    assert trust.freshness_points(None, datetime.now(UTC)) == 0


def test_schema_changes_raise_an_alert(demo_org, tmp_path):
    folder = tmp_path / "drive"
    folder.mkdir()
    sheet = folder / "stock.csv"
    sheet.write_text("sku,qty,warehouse\nA1,5,PUN\n")
    with system_session() as db:
        org_id = db.scalar(select(Org.id))
        source = create_source(db, org_id, "files", "stock", {"path": str(folder)})
        db.commit()
        source_id = source.id

    sync_source(source_id)
    assert open_alerts("schema_change") == {}  # a new asset isn't a change

    sheet.write_text("sku,qty,bin\nA1,five,B-2\n")
    sync_source(source_id)
    message = open_alerts("schema_change")["stock.csv"]
    assert "added bin" in message
    assert "removed warehouse" in message
    assert "qty is now str" in message or "qty is now object" in message


def test_members_see_alerts_for_what_they_can_see_and_owners_resolve(synced_demo, client):
    paths = ids_by_path()
    use(paths.values())
    refresh()

    priya = log_in(client, "priya.sharma@chaiwala.co")
    listed = client.get("/api/alerts", headers=priya).json()
    listed_paths = {alert["asset_path"] for alert in listed}
    assert "sales_db.public.orders" in listed_paths
    assert not any(path.startswith(("finance", "hr/")) for path in listed_paths)
    orders_alert = next(a for a in listed if a["asset_path"] == "sales_db.public.orders")
    assert orders_alert["can_resolve"] is False
    response = client.post(f"/api/alerts/{orders_alert['id']}/resolve", headers=priya)
    assert response.status_code == 403

    admin = log_in(client, "admin@chaiwala.co")
    resolved = client.post(f"/api/alerts/{orders_alert['id']}/resolve", headers=admin)
    assert resolved.status_code == 200
    assert resolved.json()["resolved_at"] is not None
    assert orders_alert["id"] in [
        a["id"] for a in client.get("/api/alerts?status=resolved", headers=admin).json()
    ]

    # Alerts on assets the user can't see don't exist as far as they know.
    finance_alert = next(
        a
        for a in client.get("/api/alerts", headers=admin).json()
        if a["asset_path"] == "finance/metrics_policy.pdf"
    )
    response = client.post(f"/api/alerts/{finance_alert['id']}/resolve", headers=priya)
    assert response.status_code == 404
