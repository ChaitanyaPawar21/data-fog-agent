"""Syncing a source into the catalogue. Runs in the worker, never inside an API request."""

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.connectors import files, postgres, slack_export
from app.db import system_session
from app.models import (
    Alert,
    Asset,
    AssetColumn,
    AssetPermission,
    Group,
    KnowledgeSnippet,
    Source,
    SyncRun,
)
from app.source_config import decrypt_config, encrypt_config

CONNECTORS = {"postgres": postgres, "files": files, "slack_export": slack_export}


def create_source(db: Session, org_id: int, source_type: str, name: str, settings: dict) -> Source:
    source = Source(
        org_id=org_id, type=source_type, name=name, encrypted_config=encrypt_config(settings)
    )
    db.add(source)
    db.flush()
    return source


def sync_source(source_id: int) -> list[int]:
    """Runs the source's connector and updates the catalogue. Returns ids of changed assets."""
    with system_session() as db:
        source = db.get(Source, source_id)
        run = SyncRun(org_id=source.org_id, source_id=source.id)
        source.status = "syncing"
        db.add(run)
        db.commit()

        try:
            connector = CONNECTORS[source.type]
            changed, new_cursor = connector.list_assets(
                decrypt_config(source.encrypted_config), source.sync_cursor or {}
            )
            changed_ids = [save_asset(db, source, found) for found in changed]
            present_paths = list(new_cursor)
            now = datetime.now(UTC)

            # Anything the connector no longer reports is gone from the source. Keep the row.
            db.execute(
                update(Asset)
                .where(Asset.source_id == source.id, Asset.path.not_in(present_paths))
                .values(archived=True)
            )
            db.execute(
                update(Asset)
                .where(Asset.source_id == source.id, Asset.path.in_(present_paths))
                .values(last_seen_at=now)
            )

            source.sync_cursor = new_cursor
            source.last_synced_at = now
            source.status = "idle"
            source.last_error = None
            run.status = "done"
            run.finished_at = now
            run.assets_seen = len(present_paths)
            db.commit()
            return changed_ids
        except Exception as error:
            db.rollback()
            source.status = "error"
            source.last_error = str(error)
            run.status = "failed"
            run.error = str(error)
            run.finished_at = datetime.now(UTC)
            db.commit()
            raise


def save_asset(db: Session, source: Source, found: dict) -> int:
    asset = db.scalar(
        select(Asset).where(Asset.source_id == source.id, Asset.path == found["path"])
    )
    is_new = asset is None
    if is_new:
        asset = Asset(org_id=source.org_id, source_id=source.id, path=found["path"])
        db.add(asset)

    asset.type = found["type"]
    asset.name = found["name"]
    asset.row_count = found["row_count"]
    asset.size_bytes = found["size_bytes"]
    asset.last_modified_at = found["last_modified_at"]
    asset.content_hash = found["content_hash"]
    asset.archived = False
    # The embeddings job picks this up again with the new name, columns and so on.
    asset.embedding = None
    db.flush()

    changes = save_columns(db, asset, found["columns"])
    if changes and not is_new:
        db.add(
            Alert(
                org_id=asset.org_id,
                asset_id=asset.id,
                type="schema_change",
                message=f"Columns changed in {asset.name}: {'; '.join(changes)}.",
            )
        )
    save_permissions(db, asset, found["groups"])
    if "snippets" in found:
        save_snippets(db, asset, found["snippets"])
    return asset.id


def save_columns(db: Session, asset: Asset, found_columns: list[dict]) -> list[str]:
    """Brings the asset's columns in line with the source. Returns what changed, in words."""
    # Update columns in place so descriptions and PII tags survive a re-sync.
    existing = {
        column.name: column
        for column in db.scalars(select(AssetColumn).where(AssetColumn.asset_id == asset.id))
    }
    added, retyped = [], []
    for found in found_columns:
        column = existing.pop(found["name"], None)
        if column is None:
            column = AssetColumn(org_id=asset.org_id, asset_id=asset.id, name=found["name"])
            db.add(column)
            added.append(found["name"])
        elif column.data_type != found["data_type"]:
            retyped.append(f"{column.name} is now {found['data_type']} (was {column.data_type})")
        column.data_type = found["data_type"]
    # Columns that were dropped in the source. This is catalogue metadata, not user data.
    for removed in existing.values():
        db.delete(removed)

    changes = []
    if added:
        changes.append("added " + ", ".join(added))
    if existing:
        changes.append("removed " + ", ".join(existing))
    return changes + retyped


def save_permissions(db: Session, asset: Asset, group_names: list[str]):
    # A group name that doesn't exist in the org is skipped, so the asset stays admin-only.
    groups = db.scalars(
        select(Group).where(Group.org_id == asset.org_id, Group.name.in_(group_names))
    ).all()
    db.execute(delete(AssetPermission).where(AssetPermission.asset_id == asset.id))
    for group in groups:
        db.add(AssetPermission(asset_id=asset.id, group_id=group.id, org_id=asset.org_id))


def save_snippets(db: Session, asset: Asset, snippets: list[dict]):
    # Snippets are copies of the channel's messages, rebuilt from the export each time.
    db.execute(delete(KnowledgeSnippet).where(KnowledgeSnippet.asset_id == asset.id))
    for snippet in snippets:
        db.add(
            KnowledgeSnippet(
                org_id=asset.org_id, source_id=asset.source_id, asset_id=asset.id, **snippet
            )
        )
