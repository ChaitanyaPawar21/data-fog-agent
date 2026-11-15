"""The MCP server, called the way an MCP client does: JSON-RPC over POST /mcp."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import system_session
from app.main import app
from app.models import ApiToken, Asset, AuditLog, Proposal, Source
from app.pii import PHONE
from app.pii_scan import scan_asset
from app.routers.tokens import hash_token
from tests.conftest import log_in


@pytest.fixture(scope="module")
def mcp_client():
    # The MCP server runs inside the app's lifespan, which a plain TestClient doesn't start.
    # One client for the whole file: the server can only be started once per process.
    with TestClient(app) as client:
        yield client


def new_token(client, email: str) -> dict:
    """Creates an API token for a demo user. Returns the header an MCP client would send."""
    response = client.post("/api/tokens", json={"name": "test"}, headers=log_in(client, email))
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['token']}"}


def rpc(client, headers: dict, method: str, params: dict | None = None):
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    accept = {"Accept": "application/json, text/event-stream"}
    return client.post("/mcp", json=request, headers=headers | accept)


def call_tool(client, headers: dict, name: str, **arguments) -> dict:
    response = rpc(client, headers, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def asset_id(path: str) -> int:
    with system_session() as db:
        return db.scalar(select(Asset.id).where(Asset.path == path))


def source_id(name: str) -> int:
    with system_session() as db:
        return db.scalar(select(Source.id).where(Source.name == name))


def audit_entries(action: str) -> list[AuditLog]:
    with system_session() as db:
        return db.scalars(select(AuditLog).where(AuditLog.action == action)).all()


# ---------------------------------------------------------------- tokens


def test_a_token_is_shown_once_stored_hashed_and_only_its_owner_can_revoke_it(demo_org, client):
    priya = log_in(client, "priya.sharma@chaiwala.co")
    created = client.post("/api/tokens", json={"name": "Claude Desktop"}, headers=priya).json()
    assert created["token"].startswith("dfa_")

    listed = client.get("/api/tokens", headers=priya).json()
    assert [(t["name"], t["last_used_at"]) for t in listed] == [("Claude Desktop", None)]
    assert "token" not in listed[0]
    with system_session() as db:
        stored = db.scalar(select(ApiToken))
        assert stored.token_hash == hash_token(created["token"])
        assert created["token"] not in str(stored.__dict__)

    rahul = log_in(client, "rahul.verma@chaiwala.co")
    assert client.get("/api/tokens", headers=rahul).json() == []
    assert client.delete(f"/api/tokens/{created['id']}", headers=rahul).status_code == 404

    assert client.delete(f"/api/tokens/{created['id']}", headers=priya).status_code == 204
    assert client.get("/api/tokens", headers=priya).json() == []
    assert [e.details["name"] for e in audit_entries("revoke_api_token")] == ["Claude Desktop"]


def test_token_names_are_checked(demo_org, client):
    priya = log_in(client, "priya.sharma@chaiwala.co")
    assert client.post("/api/tokens", json={"name": ""}, headers=priya).status_code == 422
    assert client.post("/api/tokens", json={"name": "x" * 101}, headers=priya).status_code == 422
    assert client.get("/api/tokens").status_code == 401


# ---------------------------------------------------------------- the MCP endpoint


def test_mcp_needs_a_valid_token(demo_org, mcp_client):
    assert rpc(mcp_client, {}, "tools/list").status_code == 401
    assert rpc(mcp_client, {"Authorization": "Bearer dfa_wrong"}, "tools/list").status_code == 401
    # A login (JWT) token is not an API token.
    jwt = log_in(mcp_client, "priya.sharma@chaiwala.co")
    assert rpc(mcp_client, jwt, "tools/list").status_code == 401

    token = new_token(mcp_client, "priya.sharma@chaiwala.co")
    assert rpc(mcp_client, token, "tools/list").status_code == 200
    token_id = mcp_client.get("/api/tokens", headers=jwt).json()[0]["id"]
    mcp_client.delete(f"/api/tokens/{token_id}", headers=jwt)
    assert rpc(mcp_client, token, "tools/list").status_code == 401


def test_mcp_offers_the_agents_tools_plus_propose_merge(demo_org, mcp_client):
    token = new_token(mcp_client, "priya.sharma@chaiwala.co")
    tools = rpc(mcp_client, token, "tools/list").json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == {
        "search_catalog",
        "get_schema",
        "run_sql",
        "search_knowledge",
        "find_owner",
        "list_pii_columns",
        "propose_merge",
    }
    # The caller is worked out from the token, never taken as an argument.
    for tool in tools:
        assert "ctx" not in tool["inputSchema"]["properties"]


def test_mcp_tools_only_show_what_the_tokens_owner_may_see(synced_demo, mcp_client):
    priya = new_token(mcp_client, "priya.sharma@chaiwala.co")
    orders_v2 = asset_id("sales_db.public.orders_v2")
    hr_file = asset_id("hr/employee_list.xlsx")

    found = call_tool(mcp_client, priya, "search_catalog", query="orders")
    assert "sales_db.public.orders_v2" in {a["path"] for a in found["assets"]}
    found = call_tool(mcp_client, priya, "search_catalog", query="employee list")
    assert all(a["path"] != "hr/employee_list.xlsx" for a in found["assets"])
    assert "error" in call_tool(mcp_client, priya, "get_schema", asset_id=hr_file)
    assert "error" in call_tool(mcp_client, priya, "find_owner", asset_id=hr_file)

    schema = call_tool(mcp_client, priya, "get_schema", asset_id=orders_v2)
    assert schema["sql_table_name"] == "public.orders_v2"
    count = call_tool(
        mcp_client,
        priya,
        "run_sql",
        source_id=schema["source_id"],
        sql="SELECT count(*) FROM public.orders_v2",
    )
    assert count["rows"] == [[3000]]
    notes = call_tool(mcp_client, priya, "search_knowledge", query="paise")
    assert {m["channel"] for m in notes["messages"]} == {"data"}

    # Arjun is in support: the sales database doesn't exist for him.
    arjun = new_token(mcp_client, "arjun.nair@chaiwala.co")
    assert "error" in call_tool(mcp_client, arjun, "get_schema", asset_id=orders_v2)
    blocked = call_tool(
        mcp_client,
        arjun,
        "run_sql",
        source_id=schema["source_id"],
        sql="SELECT count(*) FROM public.orders_v2",
    )
    assert "error" in blocked


def test_mcp_calls_are_audited_as_mcp_and_only_select_runs(synced_demo, mcp_client):
    priya = new_token(mcp_client, "priya.sharma@chaiwala.co")
    sales = source_id("sales_db")

    call_tool(mcp_client, priya, "search_catalog", query="orders")
    call_tool(mcp_client, priya, "run_sql", source_id=sales, sql="SELECT 1 FROM public.orders")
    for sql in ["DELETE FROM public.orders", "DROP TABLE public.orders", "SELECT 1; SELECT 2"]:
        rejected = call_tool(mcp_client, priya, "run_sql", source_id=sales, sql=sql)
        assert rejected["error"].startswith("Query rejected")

    assert len(audit_entries("sql_rejected")) == 3
    for action in ["search_catalog", "run_sql", "sql_rejected"]:
        entries = audit_entries(action)
        assert entries and {entry.actor for entry in entries} == {"mcp"}, action
    still_there = call_tool(
        mcp_client, priya, "run_sql", source_id=sales, sql="SELECT count(*) FROM public.orders"
    )
    assert still_there["rows"] == [[1624]]


def test_mcp_keeps_personal_data_masked(synced_demo, mcp_client):
    customers = asset_id("sales_db.public.customers_new")
    scan_asset(customers)
    priya = new_token(mcp_client, "priya.sharma@chaiwala.co")

    rows = call_tool(
        mcp_client,
        priya,
        "run_sql",
        source_id=source_id("sales_db"),
        sql="SELECT phone FROM public.customers_new LIMIT 20",
    )["rows"]
    assert len(rows) == 20
    assert not any(PHONE.search(str(row[0])) for row in rows)

    tagged = call_tool(mcp_client, priya, "list_pii_columns")["columns"]
    assert {"asset_id": customers, "asset": "sales_db.public.customers_new"}.items() <= next(
        column for column in tagged if column["column"] == "phone"
    ).items()
    arjun = new_token(mcp_client, "arjun.nair@chaiwala.co")
    assert call_tool(mcp_client, arjun, "list_pii_columns") == {"columns": []}


def test_propose_merge_only_creates_a_pending_proposal(demo, synced_demo, mcp_client):
    output_dir, _ = demo
    files = ["sales/orders_export_jan.csv", "sales/orders_export_jan (1).csv"]
    ids = [asset_id(path) for path in files]
    priya = new_token(mcp_client, "priya.sharma@chaiwala.co")

    proposed = call_tool(mcp_client, priya, "propose_merge", asset_ids=ids)
    assert proposed["status"] == "pending"
    assert (proposed["total_rows"], proposed["merged_rows"]) == (312, 156)
    with system_session() as db:
        proposal = db.scalar(select(Proposal))
        assert (proposal.type, proposal.status, proposal.reviewed_by) == ("merge", "pending", None)
    # Nothing happened to the files.
    assert all((Path(output_dir) / "drive" / path).exists() for path in files)
    assert not (Path(output_dir) / "drive" / "sales" / "_archive").exists()
    assert {entry.actor for entry in audit_entries("propose_merge")} == {"mcp"}

    again = call_tool(mcp_client, priya, "propose_merge", asset_ids=ids)
    assert "already been proposed" in again["error"]
    one_file = call_tool(mcp_client, priya, "propose_merge", asset_ids=ids[:1])
    assert "error" in one_file
    different = [ids[0], asset_id("sales/sales_report_final.xlsx")]
    mismatch = call_tool(mcp_client, priya, "propose_merge", asset_ids=different)
    assert "same columns" in mismatch["error"]

    # Files the caller can't see can't be proposed, and look the same as missing ones.
    arjun = new_token(mcp_client, "arjun.nair@chaiwala.co")
    assert "error" in call_tool(mcp_client, arjun, "propose_merge", asset_ids=ids)
    with system_session() as db:
        assert len(db.scalars(select(Proposal)).all()) == 1


def test_mcp_is_rate_limited_per_user(demo_org, mcp_client):
    priya = new_token(mcp_client, "priya.sharma@chaiwala.co")
    statuses = [rpc(mcp_client, priya, "tools/list").status_code for _ in range(61)]
    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429

    rahul = new_token(mcp_client, "rahul.verma@chaiwala.co")
    assert rpc(mcp_client, rahul, "tools/list").status_code == 200
