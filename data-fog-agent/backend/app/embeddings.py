"""Fills in embeddings for assets and Slack snippets that don't have one yet.

Sync clears an asset's embedding when it changes, so this one job covers new assets,
changed assets and retries after a failed run.
"""

from itertools import batched

from sqlalchemy import select

from app import config, llm
from app.db import system_session
from app.models import Asset, AssetColumn, KnowledgeSnippet
from app.pii import mask_pii_in_text

BATCH_SIZE = 100


def asset_embedding_text(asset: Asset, column_names: list[str]) -> str:
    parts = [asset.name, asset.path, asset.description or "", " ".join(column_names)]
    return mask_pii_in_text("\n".join(part for part in parts if part))


def embed_missing() -> int:
    """Returns how many rows got an embedding. Does nothing without an API key."""
    if not config.EMBEDDING_API_KEY:
        return 0

    embedded = 0
    with system_session() as db:
        assets = db.scalars(
            select(Asset).where(Asset.embedding.is_(None), Asset.archived.is_(False))
        ).all()
        for batch in batched(assets, BATCH_SIZE):
            column_names = {}
            for asset_id, name in db.execute(
                select(AssetColumn.asset_id, AssetColumn.name).where(
                    AssetColumn.asset_id.in_([asset.id for asset in batch])
                )
            ):
                column_names.setdefault(asset_id, []).append(name)

            texts = [asset_embedding_text(a, column_names.get(a.id, [])) for a in batch]
            for asset, vector in zip(batch, llm.embed_texts(texts), strict=True):
                asset.embedding = vector
            db.commit()
            embedded += len(batch)

        snippets = db.scalars(select(KnowledgeSnippet).where(KnowledgeSnippet.embedding.is_(None)))
        for batch in batched(snippets.all(), BATCH_SIZE):
            # Slack messages are free text and can hold phone numbers, Aadhaar numbers and so on.
            texts = [mask_pii_in_text(snippet.text) for snippet in batch]
            for snippet, vector in zip(batch, llm.embed_texts(texts), strict=True):
                snippet.embedding = vector
            db.commit()
            embedded += len(batch)
    return embedded
