from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.models import Asset, Source, User
from app.sync import create_source
from app.worker import sync_job_id

router = APIRouter(prefix="/api/sources", tags=["sources"])

# The config key each source type can't work without.
REQUIRED_SETTING = {"postgres": "dsn", "files": "path", "slack_export": "path"}


class SourceIn(BaseModel):
    type: Literal["postgres", "files", "slack_export"]
    name: str
    config: dict


class SourceOut(BaseModel):
    """Never includes the config: it holds connection passwords."""

    id: int
    type: str
    name: str
    status: str
    last_synced_at: datetime | None
    last_error: str | None
    asset_count: int


def get_job_queue(request: Request):
    return request.app.state.job_queue


async def enqueue_sync(job_queue, source_id: int) -> bool:
    """False when a sync of this source is already queued or running."""
    job = await job_queue.enqueue_job("sync_source_job", source_id, _job_id=sync_job_id(source_id))
    return job is not None


@router.get("", response_model=list[SourceOut])
def list_sources(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    asset_count = (
        select(func.count(Asset.id))
        .where(Asset.source_id == Source.id, Asset.archived.is_(False))
        .scalar_subquery()
    )
    rows = db.execute(select(Source, asset_count).order_by(Source.name))
    return [SourceOut(**source_fields(source), asset_count=count) for source, count in rows]


def source_fields(source: Source) -> dict:
    return {
        "id": source.id,
        "type": source.type,
        "name": source.name,
        "status": source.status,
        "last_synced_at": source.last_synced_at,
        "last_error": source.last_error,
    }


@router.post("", response_model=SourceOut, status_code=201)
async def add_source(
    body: SourceIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    job_queue=Depends(get_job_queue),
):
    required = REQUIRED_SETTING[body.type]
    if not body.config.get(required):
        raise HTTPException(422, f"config.{required} is required for a {body.type} source")

    source = create_source(db, user.org_id, body.type, body.name, body.config)
    db.commit()
    await enqueue_sync(job_queue, source.id)
    return SourceOut(**source_fields(source), asset_count=0)


@router.post("/{source_id}/sync", status_code=202)
async def sync_now(
    source_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    job_queue=Depends(get_job_queue),
):
    # Row-level security hides other orgs' sources, so this also stops cross-org syncs.
    if db.get(Source, source_id) is None:
        raise HTTPException(404, "Source not found")
    return {"queued": await enqueue_sync(job_queue, source_id)}
