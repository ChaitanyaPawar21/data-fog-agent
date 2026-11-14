"""Background jobs. Run with: uv run arq app.worker.WorkerSettings"""

import asyncio

from arq import cron, func
from arq.connections import RedisSettings
from sqlalchemy import select

from app import config, docs_gen, embeddings, pii_scan, sync
from app.db import system_session
from app.models import Source


def sync_job_id(source_id: int) -> str:
    # arq won't queue a job whose id is already queued or running, so a source never
    # syncs twice at the same time.
    return f"sync-source-{source_id}"


# arq also refuses a job id while that job's result is stored (an hour by default), which
# would silently drop a second "sync now". These jobs don't need their results kept.
NO_RESULT = {"keep_result": 0}


async def sync_source_job(ctx, source_id: int) -> int:
    # The sync code is plain blocking code, so it runs in a thread to keep the worker responsive.
    changed_ids = await asyncio.to_thread(sync.sync_source, source_id)
    if changed_ids:
        await ctx["redis"].enqueue_job("embed_missing_job", _job_id="embed-missing")
    for asset_id in changed_ids:
        await ctx["redis"].enqueue_job("pii_scan_job", asset_id, _job_id=f"pii-scan-{asset_id}")
    # Every undocumented asset, not only changed ones, so a catalogue synced before
    # Auto-Docs existed gets drafts too. Assets with a pending or rejected draft are skipped.
    with system_session() as db:
        undocumented = docs_gen.assets_needing_docs(db)
    for asset_id in undocumented:
        await ctx["redis"].enqueue_job("docs_job", asset_id, _job_id=f"docs-{asset_id}")
    return len(changed_ids)


async def pii_scan_job(ctx, asset_id: int) -> list[str]:
    return await asyncio.to_thread(pii_scan.scan_asset, asset_id)


async def docs_job(ctx, asset_id: int) -> int | None:
    if not config.DOCS_API_KEY:
        return None
    # Jobs run in parallel, so this asset's pii_scan_job may not have run yet. Scanning here
    # first means a draft never sees a personal-data column before it's tagged.
    await asyncio.to_thread(pii_scan.scan_asset, asset_id)
    return await asyncio.to_thread(docs_gen.generate_docs, asset_id)


async def embed_missing_job(ctx) -> int:
    return await asyncio.to_thread(embeddings.embed_missing)


async def sync_all_sources(ctx):
    with system_session() as db:
        source_ids = db.scalars(select(Source.id)).all()
    for source_id in source_ids:
        await ctx["redis"].enqueue_job("sync_source_job", source_id, _job_id=sync_job_id(source_id))


class WorkerSettings:
    functions = [
        func(sync_source_job, **NO_RESULT),
        func(embed_missing_job, **NO_RESULT),
        func(pii_scan_job, **NO_RESULT),
        func(docs_job, **NO_RESULT),
    ]
    cron_jobs = [cron(sync_all_sources, hour={0, 6, 12, 18}, minute=0)]
    redis_settings = RedisSettings.from_dsn(config.REDIS_URL)
