import json

import pandas as pd
import psycopg
from psycopg.conninfo import make_conninfo
from sqlalchemy import select

from app.db import system_session
from app.models import Group, Source, User
from app.pii import verhoeff_is_valid
from tests.conftest import generate


def test_demo_builds_databases_files_slack_users_and_sources(demo):
    output_dir, truth = demo
    generate.create_users_and_groups()

    reader_url = make_conninfo(
        generate.ADMIN_DATABASE_URL,
        user=generate.READER_ROLE,
        password=generate.READER_PASSWORD,
        dbname="sales_db",
    )
    with psycopg.connect(reader_url) as db:
        order_count = db.execute("SELECT count(*) FROM orders_v2").fetchone()[0]
        assert order_count == truth["answers"]["orders_v2_row_count"]
        newest_old_order = db.execute("SELECT max(order_date) FROM orders").fetchone()[0]
        assert newest_old_order <= generate.OLD_ORDERS_CUTOFF
        # The reader role must not be able to write.
        try:
            db.execute("DELETE FROM orders")
            raise AssertionError("reader role was able to delete")
        except psycopg.errors.ReadOnlySqlTransaction:
            pass

    with psycopg.connect(make_conninfo(generate.ADMIN_DATABASE_URL, dbname="finance_db")) as db:
        march = db.execute(
            "SELECT net_revenue FROM revenue_monthly WHERE month = '2026-03-01'"
        ).fetchone()[0]
        assert float(march) == truth["answers"]["net_revenue_2026_03_rupees"]

    drive = output_dir / "drive"
    sales_reports, orders_exports = truth["duplicate_groups"][:2]
    for name in sales_reports["files"] + orders_exports["files"]:
        assert (drive / "sales" / name).exists()
    assert sales_reports["conflicting_keys"]
    assert orders_exports["exact_duplicate_rows"] == orders_exports["merged_rows"]
    for aadhaar in pd.read_excel(drive / "hr" / "employee_list.xlsx")["aadhaar"]:
        assert verhoeff_is_valid(aadhaar.replace(" ", ""))

    slack = output_dir / "slack"
    channels = [c["name"] for c in json.loads((slack / "channels.json").read_text())]
    assert channels == ["data", "finance", "sales"]
    assert any(
        "orders_v2" in m["text"]
        for f in (slack / "data").glob("*.json")
        for m in json.loads(f.read_text())
    )

    generate.register_demo_sources(output_dir)
    with system_session() as db:
        emails = set(db.scalars(select(User.email)))
        group_names = set(db.scalars(select(Group.name)))
        source_names = set(db.scalars(select(Source.name)))
    assert emails == {person["email"] for person in generate.PEOPLE}
    assert group_names == set(generate.GROUPS)
    assert source_names == {"sales_db", "finance_db", "support_db", "drive", "slack"}

    # Adding the same users and sources again must not fail or create duplicates.
    generate.create_users_and_groups()
    generate.register_demo_sources(output_dir)
    with system_session() as db:
        assert len(db.scalars(select(User)).all()) == len(generate.PEOPLE)
        assert len(db.scalars(select(Source)).all()) == 5
