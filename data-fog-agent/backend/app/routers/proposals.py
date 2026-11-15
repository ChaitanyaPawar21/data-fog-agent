"""Proposals the agent makes and people approve. Nothing changes until someone approves.

Two kinds: `doc` (a drafted description for one asset) and `merge` (copies of one file to
combine into one). Approving a doc applies it here; approving a merge queues the worker.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import get_current_user
from app.db import get_db
from app.models import Asset, AssetColumn, Proposal, User
from app.permissions import can_review
from app.routers.sources import get_job_queue

router = APIRouter(prefix="/api/proposals", tags=["proposals"])

# What the merge preview shows. The rest of the payload is for the worker.
MERGE_FIELDS = [
    "files",
    "key_column",
    "total_rows",
    "exact_duplicate_rows",
    "conflicting_keys",
    "conflicts",
    "merged_rows",
    "output_name",
    "output_path",
    "groups",
    "error",
]


class ProposedColumn(BaseModel):
    name: str
    data_type: str
    description: str | None


class ProposalOut(BaseModel):
    id: int
    type: str
    status: str
    created_at: datetime
    reviewed_at: datetime | None
    # The asset a doc is for; for a merge, the newest of the files.
    asset_id: int
    asset_name: str
    asset_path: str
    description: str | None = None
    columns: list[ProposedColumn] | None = None
    merge: dict | None = None


def proposal_assets(db: Session, proposal: Proposal) -> list[Asset]:
    if proposal.type == "doc":
        ids = [proposal.payload["asset_id"]]
    else:
        ids = [file["asset_id"] for file in proposal.payload["files"]]  # oldest first
    return [db.get(Asset, asset_id) for asset_id in ids]


def may_review(user: User, assets: list[Asset]) -> bool:
    """Admins, or a user who owns every asset the proposal touches."""
    return all(asset is not None and can_review(user, asset) for asset in assets)


def to_out(db: Session, proposal: Proposal, assets: list[Asset]) -> ProposalOut:
    main = assets[-1]
    out = ProposalOut(
        id=proposal.id,
        type=proposal.type,
        status=proposal.status,
        created_at=proposal.created_at,
        reviewed_at=proposal.reviewed_at,
        asset_id=main.id,
        asset_name=main.name,
        asset_path=main.path,
    )
    if proposal.type == "merge":
        out.merge = {key: proposal.payload.get(key) for key in MERGE_FIELDS}
        return out

    columns = db.scalars(
        select(AssetColumn).where(AssetColumn.asset_id == main.id).order_by(AssetColumn.id)
    )
    lines = proposal.payload["columns"]
    out.description = proposal.payload["description"]
    out.columns = [
        ProposedColumn(name=c.name, data_type=c.data_type, description=lines.get(c.name))
        for c in columns
    ]
    return out


@router.get("", response_model=list[ProposalOut])
def list_proposals(
    status: Literal["pending", "approved", "rejected", "done", "failed", "undone"] = "pending",
    type: Literal["doc", "merge"] | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The proposals this user may review: all for admins, their own assets' for owners."""
    query = (
        select(Proposal)
        .where(Proposal.status == status)
        .order_by(Proposal.created_at.desc())
        .limit(200)
    )
    if type is not None:
        query = query.where(Proposal.type == type)
    found = []
    for proposal in db.scalars(query):
        assets = proposal_assets(db, proposal)
        if may_review(user, assets):
            found.append(to_out(db, proposal, assets))
    return found


def get_reviewable(
    db: Session, user: User, proposal_id: int, status: str = "pending"
) -> tuple[Proposal, list[Asset]]:
    proposal = db.get(Proposal, proposal_id)
    assets = proposal_assets(db, proposal) if proposal else []
    # Not yours to review looks the same as missing.
    if proposal is None or not may_review(user, assets):
        raise HTTPException(404, "Proposal not found")
    if proposal.status != status:
        raise HTTPException(409, f"This proposal is {proposal.status}.")
    return proposal, assets


def mark_reviewed(proposal: Proposal, user: User, status: str):
    proposal.status = status
    proposal.reviewed_by = user.id
    proposal.reviewed_at = datetime.now(UTC)


def apply_doc(db: Session, proposal: Proposal, asset: Asset):
    asset.description = proposal.payload["description"]
    asset.description_status = "approved"
    lines = proposal.payload["columns"]
    for column in db.scalars(select(AssetColumn).where(AssetColumn.asset_id == asset.id)):
        # A column someone already approved keeps its description.
        if column.name in lines and column.description_status != "approved":
            column.description = lines[column.name]
            column.description_status = "approved"
    # The description is part of the asset's embedding, so search should pick it up.
    asset.embedding = None


@router.post("/{proposal_id}/approve", response_model=ProposalOut)
async def approve(
    proposal_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    job_queue=Depends(get_job_queue),
):
    proposal, assets = get_reviewable(db, user, proposal_id)
    if proposal.type == "doc":
        apply_doc(db, proposal, assets[0])
    mark_reviewed(proposal, user, "approved")
    audit(
        db,
        user,
        "user",
        "approve_proposal",
        f"proposal:{proposal.id}",
        {"type": proposal.type, "asset_ids": [a.id for a in assets]},
    )
    db.commit()

    if proposal.type == "doc":
        await job_queue.enqueue_job("embed_missing_job", _job_id="embed-missing")
    else:
        # Moving files is slow work, so the worker does it; the status turns done or failed.
        job_id = f"merge-{proposal.id}"
        await job_queue.enqueue_job("execute_merge_job", proposal.id, _job_id=job_id)
    return to_out(db, proposal, assets)


@router.post("/{proposal_id}/reject", response_model=ProposalOut)
def reject(proposal_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proposal, assets = get_reviewable(db, user, proposal_id)
    if proposal.type == "doc" and assets[0].description_status == "proposed":
        assets[0].description_status = "none"
    mark_reviewed(proposal, user, "rejected")
    audit(
        db,
        user,
        "user",
        "reject_proposal",
        f"proposal:{proposal.id}",
        {"type": proposal.type, "asset_ids": [a.id for a in assets]},
    )
    db.commit()
    return to_out(db, proposal, assets)


@router.post("/{proposal_id}/undo", response_model=ProposalOut, status_code=202)
async def undo(
    proposal_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    job_queue=Depends(get_job_queue),
):
    """Undoes a completed merge: the originals come back, the merged file is archived."""
    proposal, assets = get_reviewable(db, user, proposal_id, status="done")
    if proposal.type != "merge":
        raise HTTPException(409, "Only merges can be undone.")
    audit(db, user, "user", "request_undo", f"proposal:{proposal.id}", None)
    db.commit()
    job_id = f"undo-{proposal.id}"
    await job_queue.enqueue_job("undo_merge_job", proposal.id, user.id, _job_id=job_id)
    return to_out(db, proposal, assets)
