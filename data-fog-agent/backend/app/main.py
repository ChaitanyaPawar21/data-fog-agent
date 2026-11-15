from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from sqlalchemy import text
from starlette.routing import Route

from app import auth, config, mcp_server
from app.db import engine
from app.routers import alerts, catalog, chat, proposals, sources, tokens


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The API only queues jobs. The worker runs them.
    app.state.job_queue = await create_pool(RedisSettings.from_dsn(config.REDIS_URL))
    async with mcp_server.mcp.session_manager.run():
        yield
    await app.state.job_queue.aclose()


app = FastAPI(title="Data Fog Agent", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(sources.router)
app.include_router(chat.router)
app.include_router(proposals.router)
app.include_router(alerts.router)
app.include_router(tokens.router)
# A plain route, not a mount: a mount would answer /mcp with a redirect to /mcp/.
app.router.routes.append(Route("/mcp", endpoint=mcp_server.asgi_app))


@app.get("/api/health")
def health():
    """For uptime checks: the API is up and can reach the database."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"ok": True}
