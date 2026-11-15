"""Alerts: stale data, schema changes and newly found personal data."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import get_current_user
from app.db import get_db
from app.models import Alert, Asset, User
from app.permissions import can_review, visible_asset_ids

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class AlertOut(BaseModel):
    id: int
    type: str
    message: str
    created_at: datetime
    resolved_at: datetime | None
    asset_id: int
    asset_name: str
    asset_path: str
    can_resolve: bool


def to_out(alert: Alert, asset: Asset, user: User) -> AlertOut:
    return AlertOut(
        id=alert.id,
        type=alert.type,
        message=alert.message,
        created_at=alert.created_at,
        resolved_at=alert.resolved_at,
        asset_id=asset.id,
        asset_name=asset.name,
        asset_path=asset.path,
        can_resolve=can_review(user, asset),
    )


@router.get("", response_model=list[AlertOut])
def list_alerts(
    status: Literal["open", "resolved"] = "open",
    type: Literal["stale", "schema_change", "pii_found"] | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alerts on assets the user can see, newest first."""
    query = (
        select(Alert, Asset)
        .join(Asset, Asset.id == Alert.asset_id)
        .where(Asset.id.in_(visible_asset_ids(user)))
        .where(Alert.resolved_at.is_(None) if status == "open" else Alert.resolved_at.is_not(None))
        .order_by(Alert.created_at.desc())
        .limit(200)
    )
    if type is not None:
        query = query.where(Alert.type == type)
    return [to_out(alert, asset, user) for alert, asset in db.execute(query)]


@router.post("/{alert_id}/resolve", response_model=AlertOut)
def resolve(alert_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.execute(
        select(Alert, Asset)
        .join(Asset, Asset.id == Alert.asset_id)
        .where(Alert.id == alert_id, Asset.id.in_(visible_asset_ids(user)))
    ).first()
    if row is None:
        raise HTTPException(404, "Alert not found")
    alert, asset = row
    # Same rule as proposals: admins, or the asset's owner.
    if not can_review(user, asset):
        raise HTTPException(403, "Only an admin or the asset's owner can resolve this.")
    if alert.resolved_at is None:
        alert.resolved_at = datetime.now(UTC)
        audit(db, user, "user", "resolve_alert", f"alert:{alert.id}", {"asset_id": asset.id})
        db.commit()
    return to_out(alert, asset, user)
