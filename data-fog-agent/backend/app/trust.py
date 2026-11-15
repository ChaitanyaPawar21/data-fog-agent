"""Trust scores (0-100) and stale alerts. Recomputed by a daily worker job and after syncs.

| Factor                                                   | Points |
| Freshness: modified within 7 / 30 / 90 days              | 30 / 20 / 10 |
| Has an owner                                             | 20 |
| Approved description                                     | 20 |
| Used by the agent or MCP in the last 30 days             | 15 |
| Not an older copy of another asset                       | 15 |
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import system_session
from app.models import Alert, Asset, AssetColumn, AuditLog, Org

STALE_AFTER = timedelta(days=90)
RECENT_USE = timedelta(days=30)
# Two assets of the same kind are copies of each other when they share at least this many
# columns and this share of all their column names.
MIN_SHARED_COLUMNS = 3
MIN_COLUMN_OVERLAP = 0.6


def freshness_points(last_modified: datetime | None, now: datetime) -> int:
    if last_modified is None:
        return 0  # unknown is not fresh
    age = now - last_modified
    if age <= timedelta(days=7):
        return 30
    if age <= timedelta(days=30):
        return 20
    if age <= STALE_AFTER:
        return 10
    return 0


def recently_used_ids(db: Session, org_id: int, now: datetime) -> set[int]:
    """Assets the agent or MCP read, searched or queried in the last 30 days."""
    used = set()
    for details in db.scalars(
        select(AuditLog.details).where(
            AuditLog.org_id == org_id,
            AuditLog.actor.in_(["agent", "mcp"]),
            AuditLog.created_at >= now - RECENT_USE,
        )
    ):
        details = details or {}
        used.update(i for i in details.get("asset_ids") or [] if isinstance(i, int))
        if isinstance(details.get("asset_id"), int):
            used.add(details["asset_id"])
    return used


def connected_groups(items: list, linked) -> list[list]:
    """Splits items into groups, where linked(a, b) joins two items and links chain:
    a copy of a copy is in the same group. Single items come back as groups of one."""
    parent = list(range(len(items)))

    def root(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    for i, j in combinations(range(len(items)), 2):
        if linked(items[i], items[j]):
            parent[root(i)] = root(j)
    groups = defaultdict(list)
    for i, item in enumerate(items):
        groups[root(i)].append(item)
    return list(groups.values())


def older_copy_ids(db: Session, org_id: int) -> set[int]:
    """Assets that look like an older copy of another one. The newest of each group is kept.

    ponytail: compares column names only, pair by pair (fine for thousands of assets). Phase 7's
    merge detection compares the values too; use its groups here once it exists.
    """
    assets = db.scalars(
        select(Asset).where(
            Asset.org_id == org_id, Asset.archived.is_(False), Asset.type.in_(["table", "file"])
        )
    ).all()
    columns = defaultdict(set)
    for asset_id, name in db.execute(
        select(AssetColumn.asset_id, AssetColumn.name).where(
            AssetColumn.asset_id.in_([a.id for a in assets])
        )
    ):
        columns[asset_id].add(name.lower())

    def look_like_copies(a: Asset, b: Asset) -> bool:
        shared = len(columns[a.id] & columns[b.id])
        return (
            a.type == b.type
            and shared >= MIN_SHARED_COLUMNS
            and shared / len(columns[a.id] | columns[b.id]) >= MIN_COLUMN_OVERLAP
        )

    oldest_first = datetime.min.replace(tzinfo=UTC)
    older = set()
    for members in connected_groups(assets, look_like_copies):
        newest = max(members, key=lambda a: a.last_modified_at or oldest_first)
        older.update(a.id for a in members if a is not newest)
    return older


def trust_factors(asset: Asset, used: set[int], older_copies: set[int], now: datetime) -> dict:
    return {
        "freshness": freshness_points(asset.last_modified_at, now),
        "owner": 20 if asset.owner_user_id else 0,
        "approved_description": 20 if asset.description_status == "approved" else 0,
        "recently_used": 15 if asset.id in used else 0,
        "not_a_copy": 0 if asset.id in older_copies else 15,
    }


def asset_trust_factors(db: Session, asset: Asset) -> dict:
    """The points behind one asset's score, for the catalogue's detail page."""
    now = datetime.now(UTC)
    return trust_factors(
        asset, recently_used_ids(db, asset.org_id, now), older_copy_ids(db, asset.org_id), now
    )


def is_stale(asset: Asset, used: set[int], now: datetime) -> bool:
    """Not modified for 90 days but still being used: someone may be relying on old data.

    Not for Slack channels: a quiet channel isn't stale data, it just has no new messages.
    """
    return (
        asset.type != "channel"
        and asset.last_modified_at is not None
        and now - asset.last_modified_at > STALE_AFTER
        and asset.id in used
    )


def refresh_org(db: Session, org_id: int, now: datetime) -> int:
    """Rescores every live asset and raises stale alerts. Returns how many alerts were raised."""
    used = recently_used_ids(db, org_id, now)
    older_copies = older_copy_ids(db, org_id)
    raised = 0
    for asset in db.scalars(select(Asset).where(Asset.org_id == org_id, Asset.archived.is_(False))):
        asset.trust_score = sum(trust_factors(asset, used, older_copies, now).values())

        stale_alerts = select(Alert).where(Alert.asset_id == asset.id, Alert.type == "stale")
        if is_stale(asset, used, now):
            # One alert per stale spell: none since the data last changed.
            already = db.scalar(stale_alerts.where(Alert.created_at > asset.last_modified_at))
            if already is None:
                days = (now - asset.last_modified_at).days
                db.add(
                    Alert(
                        org_id=org_id,
                        asset_id=asset.id,
                        type="stale",
                        message=f"{asset.name} hasn't changed in {days} days but is still "
                        "being used. Check it's still the right source.",
                    )
                )
                raised += 1
        elif freshness_points(asset.last_modified_at, now):
            # The data changed again, so an open stale alert no longer holds.
            for alert in db.scalars(stale_alerts.where(Alert.resolved_at.is_(None))):
                alert.resolved_at = now
    return raised


def refresh_all() -> int:
    """The worker job: every org, one transaction each."""
    now = datetime.now(UTC)
    raised = 0
    with system_session() as db:
        org_ids = db.scalars(select(Org.id)).all()
    for org_id in org_ids:
        with system_session() as db:
            raised += refresh_org(db, org_id, now)
            db.commit()
    return raised
