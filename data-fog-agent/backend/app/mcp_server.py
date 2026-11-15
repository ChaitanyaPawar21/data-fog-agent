"""The MCP server: the catalogue and the agent's tools for Claude and other MCP clients.

Served at /mcp (Streamable HTTP). A client sends a personal API token from the Settings page
as `Authorization: Bearer dfa_...` and acts as that user. Every tool runs through
`agent.run_tool`, the same functions and permission checks the chat agent uses, and every
call is written to the audit log with actor "mcp".
"""

import asyncio

from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from app import agent
from app.db import SessionLocal
from app.models import User
from app.rate_limit import check_rate_limit
from app.routers.tokens import user_for_token

INSTRUCTIONS = """\
A company's data catalogue: database tables, files and Slack channels, limited to what the \
token's owner may see. Find assets with search_catalog, read their columns with get_schema \
before writing SQL, and check search_knowledge for the team's rules (which table is current, \
units, metric definitions). Prefer the asset with the highest trust score when several hold \
the same data. Values like XXXX1234 are masked personal data. Everything is read-only; \
propose_merge only creates a proposal that a person has to approve.\
"""

mcp = MCPServer("Data Fog Agent", instructions=INSTRUCTIONS)

DESCRIPTIONS = {t["function"]["name"]: t["function"]["description"] for t in agent.TOOLS}


def run_as_caller(ctx: Context, name: str, **arguments) -> dict:
    user_id, org_id = ctx.request_context.request.scope["mcp_user"]
    with SessionLocal(info={"org_id": org_id}) as db:
        user = db.get(User, user_id)
        result, _ = agent.run_tool(db, user, name, arguments, actor="mcp")
        db.commit()
    return result


async def call(ctx: Context, name: str, **arguments) -> dict:
    # The tools are plain blocking code (database queries), so they run in a thread.
    return await asyncio.to_thread(run_as_caller, ctx, name, **arguments)


@mcp.tool(description=DESCRIPTIONS["search_catalog"])
async def search_catalog(query: str, ctx: Context) -> dict:
    return await call(ctx, "search_catalog", query=query)


@mcp.tool(description=DESCRIPTIONS["get_schema"])
async def get_schema(asset_id: int, ctx: Context) -> dict:
    return await call(ctx, "get_schema", asset_id=asset_id)


@mcp.tool(description=DESCRIPTIONS["run_sql"])
async def run_sql(source_id: int, sql: str, ctx: Context) -> dict:
    return await call(ctx, "run_sql", source_id=source_id, sql=sql)


@mcp.tool(description=DESCRIPTIONS["search_knowledge"])
async def search_knowledge(query: str, ctx: Context) -> dict:
    return await call(ctx, "search_knowledge", query=query)


@mcp.tool(description=DESCRIPTIONS["find_owner"])
async def find_owner(asset_id: int, ctx: Context) -> dict:
    return await call(ctx, "find_owner", asset_id=asset_id)


@mcp.tool(description=DESCRIPTIONS["list_pii_columns"])
async def list_pii_columns(ctx: Context) -> dict:
    return await call(ctx, "list_pii_columns")


@mcp.tool(
    description="Propose merging csv/xlsx files that are copies of each other (same columns, "
    "same file source), by asset id. This only creates a pending proposal with a preview: "
    "nothing is merged until an admin or the files' owner approves it."
)
async def propose_merge(asset_ids: list[int], ctx: Context) -> dict:
    return await call(ctx, "propose_merge", asset_ids=asset_ids)


class TokenAuth:
    """Lets a request through to the MCP server only with a valid API token."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        header = Headers(scope=scope).get("authorization", "")
        caller = None
        if header.lower().startswith("bearer "):
            caller = await asyncio.to_thread(user_for_token, header[7:].strip())
        if caller is None:
            response = JSONResponse(
                {"detail": "Send an API token from the Settings page as a Bearer token."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            return await response(scope, receive, send)

        try:
            # Twice the chat limit: a client also spends requests on the handshake and on
            # listing the tools.
            await asyncio.to_thread(check_rate_limit, "mcp", str(caller[0]), 60)
        except HTTPException as limit:
            response = JSONResponse({"detail": limit.detail}, status_code=limit.status_code)
            return await response(scope, receive, send)

        scope["mcp_user"] = caller
        await self.app(scope, receive, send)


# main.py adds this at /mcp and runs `mcp.session_manager` for the life of the app.
asgi_app = TokenAuth(
    mcp.streamable_http_app(
        streamable_http_path="/mcp",
        # No sessions kept in memory: the API runs as several worker processes, and a
        # client's next request can land on any of them.
        stateless_http=True,
        json_response=True,
        # The SDK's Host-header check is for servers on localhost with no login. Here every
        # request needs a token, and the real host name is whatever domain Caddy serves.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
)
