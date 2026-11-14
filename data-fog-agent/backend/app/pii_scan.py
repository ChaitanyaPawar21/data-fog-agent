"""PII Guard: tags columns that hold personal data. Runs in the worker after a sync."""

from sqlalchemy import select

from app.db import system_session
from app.models import Alert, Asset, AssetColumn, Source
from app.pii import column_pii_types
from app.source_config import decrypt_config
from app.sync import CONNECTORS


def scan_asset(asset_id: int) -> list[str]:
    """Samples the asset's columns and tags the ones that hold personal data.

    Raises a `pii_found` alert when a column gets a new kind of PII. Returns a line per
    newly tagged column. The sampled values are never stored, only the kind of PII.
    """
    with system_session() as db:
        asset = db.get(Asset, asset_id)
        source = db.get(Source, asset.source_id)
        connector = CONNECTORS[source.type]
        columns = db.scalars(select(AssetColumn).where(AssetColumn.asset_id == asset.id)).all()
        # Slack channels have no columns and no sample_values.
        if asset.archived or not columns or not hasattr(connector, "sample_values"):
            return []

        samples = connector.sample_values(
            decrypt_config(source.encrypted_config), asset.path, [c.name for c in columns]
        )
        newly_found = []
        for column in columns:
            found = column_pii_types(column.name, column.data_type, samples.get(column.name, []))
            known = set(column.pii_type.split(",")) if column.pii_type else set()
            # Tags only grow. A later sample that happens to miss a match must not unmask
            # the column; removing a tag is a person's call.
            if found - known:
                column.pii_type = ",".join(sorted(known | found))
                newly_found.append(f"{column.name} ({', '.join(sorted(found - known))})")

        if newly_found:
            db.add(
                Alert(
                    org_id=asset.org_id,
                    asset_id=asset.id,
                    type="pii_found",
                    message=f"Personal data found in {asset.name}: {'; '.join(newly_found)}.",
                )
            )
        db.commit()
        return newly_found
