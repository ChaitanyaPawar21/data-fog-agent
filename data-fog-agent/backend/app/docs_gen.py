"""Auto-Docs: the LLM drafts descriptions for undocumented assets, people approve them.

Runs in the worker. A draft is saved as a `doc` proposal; nothing reaches the catalogue until
an owner or admin approves it (see routers/proposals.py).
"""

import json
import re
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import system_session
from app.llm import OpenAI
from app.models import Asset, AssetColumn, AssetPermission, KnowledgeSnippet, Proposal, Source
from app.pii import mask_pii_in_text
from app.source_config import decrypt_config
from app.sync import CONNECTORS

SAMPLES_PER_COLUMN = 5
MAX_SNIPPETS = 8

PROMPT = """You write short documentation for a company's data catalogue.

Asset: {name}
Kind: {type}
Path: {path}
Columns (name, type, a few example values):
{columns}

Team chat messages that may mention it:
{snippets}

Reply with JSON only, no other text:
{{"description": "<one paragraph: what this holds, what it's for, any caveats the notes mention>",
 "columns": {{"<column name>": "<one line>", ...}}}}
Describe every column listed: what it means, not its example values. For a personal-data
column say what it holds, like "Customer's full name". Don't invent facts the input doesn't
support."""

# Reasoning models think before answering, and that counts against max_tokens.
MAX_TOKENS = 16000


def assets_needing_docs(db: Session) -> list[int]:
    """Undocumented assets with no pending or rejected doc proposal.

    ponytail: a rejected draft is never retried automatically. Add a "draft again" button if
    people want a second try.
    """
    already_drafted = select(Proposal.payload["asset_id"].as_integer()).where(
        Proposal.type == "doc", Proposal.status.in_(["pending", "rejected"])
    )
    return list(
        db.scalars(
            select(Asset.id).where(
                Asset.archived.is_(False),
                Asset.description_status == "none",
                Asset.id.not_in(already_drafted),
            )
        )
    )


def safe_samples(source: Source, asset: Asset, columns: list[AssetColumn]) -> dict:
    """A few example values per column, for columns that aren't tagged as personal data.

    Tagged columns get no values at all: the model is told the kind of data instead, which
    is safer than masked values and tells it more ("XXXXKant" says nothing).
    """
    connector = CONNECTORS[source.type]
    plain = [c.name for c in columns if not c.pii_type]
    if not plain or not hasattr(connector, "sample_values"):
        return {}
    samples = connector.sample_values(
        decrypt_config(source.encrypted_config), asset.path, plain, limit=SAMPLES_PER_COLUMN
    )
    # A second net for stray PII in columns the scan didn't tag, like a free-text field.
    return {name: [mask_pii_in_text(value) for value in values] for name, values in samples.items()}


def related_snippets(db: Session, asset: Asset) -> list[str]:
    """Slack messages about the asset, only from channels everyone who sees the asset can see.

    The approved description is shown to the asset's viewers, so it must not carry anything
    from a channel some of them can't read.
    """
    asset_groups = set(
        db.scalars(select(AssetPermission.group_id).where(AssetPermission.asset_id == asset.id))
    )
    # The worker's session skips row-level security, so stay inside the asset's org by hand.
    query = select(KnowledgeSnippet).where(KnowledgeSnippet.org_id == asset.org_id)
    if asset.type == "channel":
        query = query.where(KnowledgeSnippet.asset_id == asset.id)
    else:
        # ponytail: name match only. Use the snippet embeddings if this misses too much.
        # Notes name files without the extension: "use sales_report_FINAL_use_this".
        query = query.where(KnowledgeSnippet.text.ilike(f"%{PurePosixPath(asset.name).stem}%"))

    texts = []
    for snippet in db.scalars(query.order_by(KnowledgeSnippet.posted_at.desc())):
        channel_groups = set(
            db.scalars(
                select(AssetPermission.group_id).where(AssetPermission.asset_id == snippet.asset_id)
            )
        )
        # An admin-only asset (no groups) is only seen by admins, who see every channel.
        if asset_groups and not asset_groups <= channel_groups:
            continue
        texts.append(mask_pii_in_text(snippet.text))
        if len(texts) == MAX_SNIPPETS:
            break
    return texts


def build_prompt(asset: Asset, columns: list[AssetColumn], samples: dict, snippets: list) -> str:
    column_lines = [
        f"- {c.name} ({c.data_type}): personal data ({c.pii_type}), values withheld"
        if c.pii_type
        else f"- {c.name} ({c.data_type}): {', '.join(samples.get(c.name, [])) or 'no samples'}"
        for c in columns
    ]
    return PROMPT.format(
        name=asset.name,
        type=asset.type,
        path=asset.path,
        columns="\n".join(column_lines) or "(none)",
        snippets="\n".join(f"- {text}" for text in snippets) or "(none)",
    )


def parse_draft(reply: str, column_names: list[str]) -> dict | None:
    """The model's JSON, keeping only a string description and lines for real columns."""
    # Reasoning models may think out loud first; the JSON is the last {...} block.
    match = re.search(r"\{.*\}", reply.split("</think>")[-1], re.DOTALL)
    if not match:
        return None
    try:
        draft = json.loads(match[0])
    except json.JSONDecodeError:
        return None
    description = draft.get("description") if isinstance(draft, dict) else None
    if not isinstance(description, str) or not description.strip():
        return None
    lines = draft.get("columns") if isinstance(draft.get("columns"), dict) else {}
    return {
        "description": description.strip(),
        "columns": {
            name: lines[name].strip()
            for name in column_names
            if isinstance(lines.get(name), str) and lines[name].strip()
        },
    }


def generate_docs(asset_id: int, client=None) -> int | None:
    """Drafts docs for one asset and saves them as a pending proposal. Returns its id."""
    with system_session() as db:
        if asset_id not in assets_needing_docs(db):
            return None
        asset = db.get(Asset, asset_id)
        columns = list(
            db.scalars(
                select(AssetColumn).where(AssetColumn.asset_id == asset.id).order_by(AssetColumn.id)
            )
        )
        source = db.get(Source, asset.source_id)
        prompt = build_prompt(
            asset, columns, safe_samples(source, asset, columns), related_snippets(db, asset)
        )

        client = client or OpenAI(api_key=config.DOCS_API_KEY, base_url=config.LLM_BASE_URL)
        response = client.chat.completions.create(
            model=config.DOCS_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise ValueError(f"The model ran out of tokens drafting docs for asset {asset.id}.")
        draft = parse_draft(choice.message.content or "", [c.name for c in columns])
        if draft is None:
            raise ValueError(f"The model's draft for asset {asset.id} wasn't valid JSON.")

        proposal = Proposal(
            org_id=asset.org_id, type="doc", payload={"asset_id": asset.id, **draft}
        )
        db.add(proposal)
        asset.description_status = "proposed"
        db.commit()
        return proposal.id
