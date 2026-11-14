import json
from types import SimpleNamespace

import psycopg
import pytest
from openai.types.chat import ChatCompletionChunk
from psycopg.conninfo import make_conninfo
from sqlalchemy import select

from app import agent, config
from app.db import SessionLocal, system_session
from app.models import Asset, AuditLog, Source, User
from app.pii import AADHAAR, PAN, PHONE, is_aadhaar
from app.pii_scan import scan_asset
from tests.conftest import generate, log_in

# ---------------------------------------------------------------- a scripted model


def chunk(delta: dict, finish_reason: str | None = None) -> ChatCompletionChunk:
    return ChatCompletionChunk.model_validate(
        {
            "id": "chunk",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
    )


def text(value: str) -> list[ChatCompletionChunk]:
    half = len(value) // 2
    return [
        chunk({"role": "assistant", "content": value[:half]}),
        chunk({"content": value[half:]}),
        chunk({}, "stop"),
    ]


def call_with_raw_arguments(name: str, raw: str) -> list[ChatCompletionChunk]:
    # Like the real API: id and name first, then the arguments in two pieces.
    first = {"index": 0, "id": f"call_{name}", "type": "function"}
    return [
        chunk({"tool_calls": [first | {"function": {"name": name, "arguments": raw[:5]}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": raw[5:]}}]}),
        chunk({}, "tool_calls"),
    ]


def calls(name: str, **arguments) -> list[ChatCompletionChunk]:
    return call_with_raw_arguments(name, json.dumps(arguments))


class FakeLLM:
    """Plays back replies in order and keeps a copy of every request, as the API would see it."""

    def __init__(self, *replies: list[ChatCompletionChunk]):
        self.replies = list(replies)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=str)))
        return iter(self.replies.pop(0))


# ---------------------------------------------------------------- helpers


@pytest.fixture
def user_session(synced_demo):
    """Opens (session, user) the way a request would for a demo user, as user_session(email)."""
    sessions = []

    def open_session(email: str):
        with system_session() as db:
            user = db.scalar(select(User).where(User.email == email))
        db = SessionLocal(info={"org_id": user.org_id})
        sessions.append(db)
        return db, db.get(User, user.id)

    yield open_session
    for db in sessions:
        db.close()


def asset_id(path: str) -> int:
    with system_session() as db:
        return db.scalar(select(Asset.id).where(Asset.path == path))


def source_id(name: str) -> int:
    with system_session() as db:
        return db.scalar(select(Source.id).where(Source.name == name))


def run(db, user, question: str, llm: FakeLLM) -> tuple[list[dict], list]:
    history = []
    return list(agent.answer(db, user, history, question, client=llm)), history


# ---------------------------------------------------------------- tools


def test_tools_only_show_what_the_user_may_see(user_session):
    db, priya = user_session("priya.sharma@chaiwala.co")
    hr_file = asset_id("hr/employee_list.xlsx")

    found, _ = agent.search_catalog(db, priya, "employee list")
    assert all(a["path"] != "hr/employee_list.xlsx" for a in found["assets"])
    assert agent.get_schema(db, priya, hr_file)[0] == agent.NOT_FOUND
    assert agent.find_owner(db, priya, hr_file)[0] == agent.NOT_FOUND
    finance = agent.run_sql(db, priya, source_id("finance_db"), "SELECT * FROM revenue_monthly")
    assert "error" in finance[0]

    count, cited = agent.run_sql(db, priya, source_id("sales_db"), "SELECT count(*) FROM orders_v2")
    assert count["rows"] == [[3000]]
    assert cited == [asset_id("sales_db.public.orders_v2")]

    # "finance revenue is net of returns" was posted in #finance, which sales can't read.
    assert agent.search_knowledge(db, priya, "revenue")[0] == {"messages": []}
    notes, _ = agent.search_knowledge(db, priya, "paise")
    assert {m["channel"] for m in notes["messages"]} == {"data"}

    db, meera = user_session("meera.iyer@chaiwala.co")
    schema, _ = agent.get_schema(db, meera, hr_file)
    assert schema["columns"]
    db, anita = user_session("anita.desai@chaiwala.co")
    notes, _ = agent.search_knowledge(db, anita, "revenue")
    assert "finance" in {m["channel"] for m in notes["messages"]}


def test_rejected_and_executed_queries_are_audited(user_session):
    db, priya = user_session("priya.sharma@chaiwala.co")
    agent.run_sql(db, priya, source_id("sales_db"), "DELETE FROM orders_v2")
    agent.run_sql(db, priya, source_id("sales_db"), "SELECT count(*) FROM orders")
    db.commit()

    with system_session() as audit_db:
        actions = audit_db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all()
    assert actions == ["sql_rejected", "run_sql"]


# ---------------------------------------------------------------- the loop


def test_the_agent_runs_tools_and_cites_what_it_used(user_session):
    db, priya = user_session("priya.sharma@chaiwala.co")
    sales = source_id("sales_db")
    llm = FakeLLM(
        calls("search_knowledge", query="orders"),
        calls("run_sql", source_id=sales, sql="SELECT count(*) FROM orders_v2"),
        text("There are 3000 orders in sales_db.public.orders_v2."),
    )

    events, history = run(db, priya, "How many orders are there?", llm)

    assert [e["name"] for e in events if e["type"] == "tool"] == ["search_knowledge", "run_sql"]
    assert "".join(e["text"] for e in events if e["type"] == "text").startswith("There are 3000")
    done = events[-1]
    assert done["table"]["rows"] == [[3000]]
    assert {"sales_db.public.orders_v2", "data"} <= {s["path"] for s in done["sources"]}

    # user, then (assistant, tool result) twice, then the answer
    assert [m["role"] for m in history] == ["user"] + ["assistant", "tool"] * 2 + ["assistant"]
    assert history[3]["tool_calls"][0]["function"]["arguments"] == json.dumps(
        {"source_id": sales, "sql": "SELECT count(*) FROM orders_v2"}
    )
    assert "3000" in history[4]["content"]
    assert llm.requests[1]["tools"] == agent.TOOLS


def test_the_last_step_has_to_answer_without_tools(user_session):
    db, priya = user_session("priya.sharma@chaiwala.co")
    llm = FakeLLM(*[calls("search_catalog", query="orders")] * 7, text("Done."))

    _, history = run(db, priya, "Keep searching forever", llm)

    assert len(llm.requests) == agent.MAX_STEPS
    assert "tools" not in llm.requests[-1]
    assert llm.requests[0]["tool_choice"] == "auto"
    # The model is told it has no tools left, but that note is never saved.
    assert llm.requests[-1]["messages"][-1]["content"] == agent.LAST_STEP_NOTE
    assert all(m.get("content") != agent.LAST_STEP_NOTE for m in history)


def test_a_bad_tool_call_is_reported_back_to_the_model(user_session):
    db, priya = user_session("priya.sharma@chaiwala.co")
    llm = FakeLLM(
        call_with_raw_arguments("get_schema", '{"asset_id": "not a number"}'),
        call_with_raw_arguments("drop_table", "{}"),
        calls("get_schema", asset_id=asset_id("sales_db.public.orders_v2")),
        text("orders_v2 has an amount column."),
    )

    events, history = run(db, priya, "What columns does orders_v2 have?", llm)

    tool_results = [m["content"] for m in history if m["role"] == "tool"]
    assert "error" in json.loads(tool_results[0])
    assert "error" in json.loads(tool_results[1])
    assert json.loads(tool_results[2])["columns"]
    assert events[-1]["type"] == "done"


def test_no_unmasked_personal_data_reaches_the_model(user_session):
    # PII Guard tags the columns first, the way the worker does after a sync.
    for path in ["sales_db.public.customers_new", "finance_db.public.tbl_cst_mst"]:
        scan_asset(asset_id(path))
    db, admin = user_session("admin@chaiwala.co")
    llm = FakeLLM(
        calls("run_sql", source_id=source_id("support_db"), sql="SELECT notes FROM tickets"),
        calls("run_sql", source_id=source_id("sales_db"), sql="SELECT * FROM customers_new"),
        calls(
            "run_sql",
            source_id=source_id("finance_db"),
            sql="SELECT col_2 AS who, upper(col_5) AS place, col_6 FROM tbl_cst_mst",
        ),
        calls("search_knowledge", query="customer"),
        text("Done."),
    )

    run(db, admin, "My number is 9876543210, find tickets about me", llm)

    sent = json.dumps([request["messages"] for request in llm.requests])
    assert "9876543210" not in sent
    assert not PHONE.search(sent)
    assert not PAN.search(sent)
    assert not any(is_aadhaar(match) for match in AADHAAR.findall(sent))
    # Names, cities and addresses have no pattern, so check the real values from the source.
    with psycopg.connect(make_conninfo(generate.ADMIN_DATABASE_URL, dbname="sales_db")) as sales:
        people = sales.execute("SELECT full_name, city, address FROM customers_new").fetchall()
    for value in {value for row in people for value in row}:
        assert value not in sent


# ---------------------------------------------------------------- chat API


def read_events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data:")]


def test_chat_streams_answers_and_keeps_each_users_conversations(synced_demo, client, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    llm = FakeLLM(text("Hello! Ask me about your data."), text("orders_v2 is current."))
    monkeypatch.setattr(agent, "llm_client", lambda: llm)
    priya = log_in(client, "priya.sharma@chaiwala.co")

    first = read_events(client.post("/api/chat", json={"message": "Hi"}, headers=priya))
    conversation_id = first[0]["id"]
    assert first[-1]["type"] == "done"

    second = {"message": "Which orders table?", "conversation_id": conversation_id}
    client.post("/api/chat", json=second, headers=priya)
    # The second question is sent along with the first exchange.
    roles = [m["role"] for m in llm.requests[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]

    saved = client.get(f"/api/chat/conversations/{conversation_id}", headers=priya).json()
    assert [t["text"] for t in saved["turns"]] == [
        "Hi",
        "Hello! Ask me about your data.",
        "Which orders table?",
        "orders_v2 is current.",
    ]
    listed = client.get("/api/chat/conversations", headers=priya).json()
    assert [c["id"] for c in listed] == [conversation_id]

    rahul = log_in(client, "rahul.verma@chaiwala.co")
    assert (
        client.get(f"/api/chat/conversations/{conversation_id}", headers=rahul).status_code == 404
    )
    assert client.post("/api/chat", json=second, headers=rahul).status_code == 404


def test_chat_says_so_when_no_api_key_is_set(synced_demo, client, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    priya = log_in(client, "priya.sharma@chaiwala.co")
    assert client.post("/api/chat", json={"message": "Hi"}, headers=priya).status_code == 503


def test_chat_is_rate_limited_per_user(synced_demo, client, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "")  # stops each request before the model
    priya = log_in(client, "priya.sharma@chaiwala.co")
    statuses = [
        client.post("/api/chat", json={"message": "Hi"}, headers=priya).status_code
        for _ in range(31)
    ]
    assert statuses[:30] == [503] * 30
    assert statuses[30] == 429

    rahul = log_in(client, "rahul.verma@chaiwala.co")
    assert client.post("/api/chat", json={"message": "Hi"}, headers=rahul).status_code == 503
