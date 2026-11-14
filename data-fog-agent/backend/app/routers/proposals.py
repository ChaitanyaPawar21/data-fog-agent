"""Proposals the agent makes and people approve. Nothing changes until someone approves."""

from datetime import UTC, datetime

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


class ProposedColumn(BaseModel):
    name: str
    data_type: str
    description: str | None


class ProposalOut(BaseModel):
    id: int
    type: str
    status: str
    created_at: datetime
    asset_id: int
    asset_name: str
    asset_path: str
    description: str
    columns: list[ProposedColumn]


def to_out(db: Session, proposal: Proposal, asset: Asset) -> ProposalOut:
    columns = db.scalars(
        select(AssetColumn).where(AssetColumn.asset_id == asset.id).order_by(AssetColumn.id)
    )
    lines = proposal.payload["columns"]
    return ProposalOut(
        id=proposal.id,
        type=proposal.type,
        status=proposal.status,
        created_at=proposal.created_at,
        asset_id=asset.id,
        asset_name=asset.name,
        asset_path=asset.path,
        description=proposal.payload["description"],
        columns=[
            ProposedColumn(name=c.name, data_type=c.data_type, description=lines.get(c.name))
            for c in columns
        ],
    )


@router.get("", response_model=list[ProposalOut])
def list_proposals(
    status: str = "pending", user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """The proposals this user may review: all of them for admins, their own assets' for owners."""
    rows = db.execute(
        select(Proposal, Asset)
        .join(Asset, Asset.id == Proposal.payload["asset_id"].as_integer())
        .where(Proposal.status == status)
        .order_by(Proposal.created_at.desc())
    )
    return [to_out(db, proposal, asset) for proposal, asset in rows if can_review(user, asset)]


def get_reviewable(db: Session, user: User, proposal_id: int) -> tuple[Proposal, Asset]:
    proposal = db.get(Proposal, proposal_id)
    asset = proposal and db.get(Asset, proposal.payload["asset_id"])
    # Not yours to review looks the same as missing.
    if asset is None or not can_review(user, asset):
        raise HTTPException(404, "Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(409, f"This proposal is already {proposal.status}.")
    return proposal, asset


def mark_reviewed(proposal: Proposal, user: User, status: str):
    proposal.status = status
    proposal.reviewed_by = user.id
    proposal.reviewed_at = datetime.now(UTC)


@router.post("/{proposal_id}/approve", response_model=ProposalOut)
async def approve(
    proposal_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    job_queue=Depends(get_job_queue),
):
    proposal, asset = get_reviewable(db, user, proposal_id)

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

    mark_reviewed(proposal, user, "approved")
    audit(db, user, "user", "approve_proposal", f"proposal:{proposal.id}", {"asset_id": asset.id})
    db.commit()
    await job_queue.enqueue_job("embed_missing_job", _job_id="embed-missing")
    return to_out(db, proposal, asset)


@router.post("/{proposal_id}/reject", response_model=ProposalOut)
def reject(proposal_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proposal, asset = get_reviewable(db, user, proposal_id)
    if asset.description_status == "proposed":
        asset.description_status = "none"
    mark_reviewed(proposal, user, "rejected")
    audit(db, user, "user", "reject_proposal", f"proposal:{proposal.id}", {"asset_id": asset.id})
    db.commit()
    return to_out(db, proposal, asset)
