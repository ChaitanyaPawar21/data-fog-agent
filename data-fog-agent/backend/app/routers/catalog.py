from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, aliased

from app.auth import get_current_user
from app.db import get_db
from app.models import Asset, AssetColumn, AssetPermission, Group, Source, User
from app.permissions import visible_asset_ids

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


class AssetSummary(BaseModel):
    id: int
    type: str
    name: str
    path: str
    source_id: int
    source_name: str
    description: str | None
    owner_name: str | None
    row_count: int | None
    last_modified_at: datetime | None
    trust_score: int | None
    has_pii: bool
    archived: bool


class ColumnOut(BaseModel):
    name: str
    data_type: str
    description: str | None
    pii_type: str | None


class AssetDetail(AssetSummary):
    description_status: str
    size_bytes: int | None
    last_seen_at: datetime | None
    groups: list[str]
    columns: list[ColumnOut]


def has_pii_column():
    return exists().where(AssetColumn.asset_id == Asset.id, AssetColumn.pii_type.is_not(None))


def summary_query(user: User):
    owner = aliased(User)
    return (
        select(Asset, Source.name, owner.name, has_pii_column())
        .join(Source, Source.id == Asset.source_id)
        .outerjoin(owner, owner.id == Asset.owner_user_id)
        .where(Asset.id.in_(visible_asset_ids(user)))
    )


def to_summary(asset: Asset, source_name: str, owner_name: str | None, has_pii: bool) -> dict:
    return {
        "id": asset.id,
        "type": asset.type,
        "name": asset.name,
        "path": asset.path,
        "source_id": asset.source_id,
        "source_name": source_name,
        "description": asset.description,
        "owner_name": owner_name,
        "row_count": asset.row_count,
        "last_modified_at": asset.last_modified_at,
        "trust_score": asset.trust_score,
        "has_pii": has_pii,
        "archived": asset.archived,
    }


@router.get("/assets", response_model=list[AssetSummary])
def search_assets(
    q: str | None = None,
    source_id: int | None = None,
    type: str | None = None,
    has_pii: bool | None = None,
    include_archived: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = summary_query(user)
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                Asset.name.ilike(pattern),
                Asset.path.ilike(pattern),
                Asset.description.ilike(pattern),
            )
        )
    if source_id is not None:
        query = query.where(Asset.source_id == source_id)
    if type is not None:
        query = query.where(Asset.type == type)
    if has_pii is not None:
        query = query.where(has_pii_column() if has_pii else ~has_pii_column())
    if not include_archived:
        query = query.where(Asset.archived.is_(False))

    # ponytail: plain text match, capped at 200 rows. The agent's search_catalog (Phase 3)
    # adds vector similarity; add paging here when a catalogue outgrows 200 matches.
    query = query.order_by(Asset.trust_score.desc().nulls_last(), Asset.name).limit(200)
    return [to_summary(*row) for row in db.execute(query)]


@router.get("/assets/{asset_id}", response_model=AssetDetail)
def get_asset(asset_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.execute(summary_query(user).where(Asset.id == asset_id)).first()
    # Hidden and missing look the same, so ids can't be used to probe for assets.
    if row is None:
        raise HTTPException(404, "Asset not found")
    asset = row[0]

    columns = db.scalars(
        select(AssetColumn).where(AssetColumn.asset_id == asset.id).order_by(AssetColumn.id)
    )
    groups = db.scalars(
        select(Group.name)
        .join(AssetPermission, AssetPermission.group_id == Group.id)
        .where(AssetPermission.asset_id == asset.id)
        .order_by(Group.name)
    )
    return {
        **to_summary(*row),
        "description_status": asset.description_status,
        "size_bytes": asset.size_bytes,
        "last_seen_at": asset.last_seen_at,
        "groups": list(groups),
        "columns": [
            ColumnOut(
                name=c.name, data_type=c.data_type, description=c.description, pii_type=c.pii_type
            )
            for c in columns
        ],
    }
