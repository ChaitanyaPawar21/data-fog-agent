"""File Merger: finds copies of the same tabular file, merges them after a person approves.

`find_merge_candidates` proposes; nothing happens to any file until a `merge` proposal is
approved. Then `execute_merge` writes the merged file, moves the originals into `_archive/`
and keeps what it needs to `undo_merge`. Files are never deleted.
"""

import difflib
import json
import os
import shutil
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.connectors.files import ARCHIVE_FOLDER, read_frame
from app.db import system_session
from app.models import Asset, AssetColumn, AssetPermission, Group, Proposal, Source, User
from app.permissions import visible_asset_ids
from app.pii_scan import scan_asset
from app.source_config import decrypt_config
from app.sync import save_permissions, sync_source
from app.trust import connected_groups

TABULAR = (".csv", ".xlsx")
MIN_NAME_SIMILARITY = 0.6
MIN_ROW_OVERLAP = 0.3
MAX_CONFLICTS_SHOWN = 50


# ---------------------------------------------------------------- reading and comparing


def file_path(source: Source, asset: Asset) -> Path:
    return Path(decrypt_config(source.encrypted_config)["path"]) / asset.path


def load(path: Path) -> pd.DataFrame:
    frame = read_frame(path)
    frame.columns = [str(name).strip() for name in frame.columns]
    return frame


def line_up(frames: list[pd.DataFrame]) -> list[pd.DataFrame]:
    """Every file with the first file's column order and spelling.

    Files are matched on column names ignoring case, so "Amount" and "amount" are one column.
    """
    spelling = {name.lower(): name for name in frames[0].columns}
    return [
        frame.rename(columns=lambda name: spelling[name.lower()])[list(frames[0].columns)]
        for frame in frames
    ]


def name_similarity(a: Asset, b: Asset) -> float:
    return difflib.SequenceMatcher(
        None, Path(a.name).stem.lower(), Path(b.name).stem.lower()
    ).ratio()


def row_overlap(a: pd.DataFrame, b: pd.DataFrame) -> float:
    """Share of the smaller file's rows that also appear, whole, in the other file."""
    smaller, larger = line_up(sorted([a, b], key=len))
    if smaller.empty:
        return 0.0
    shared = smaller.merge(larger.drop_duplicates(), how="inner", on=list(smaller.columns))
    return len(shared) / len(smaller)


def pick_key(frames: list[pd.DataFrame]) -> str | None:
    """A column named like *_id that is filled and unique in every file. None: whole rows."""
    for column in frames[0].columns:
        if column.lower().endswith("_id") and all(
            frame[column].notna().all() and frame[column].is_unique for frame in frames
        ):
            return column
    return None


def jsonable(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def merge_frames(frames: list[pd.DataFrame], key: str | None) -> dict:
    """Merges files given oldest first. The newest file's version of a row wins.

    Returns the merged frame and the numbers a reviewer needs to judge it.
    """
    combined = pd.concat(frames, ignore_index=True)
    distinct = combined.drop_duplicates()
    if key is None:
        merged, conflicting = distinct, distinct.iloc[0:0]
    else:
        merged = combined.drop_duplicates(subset=key, keep="last")
        conflicting = distinct[distinct.duplicated(subset=key, keep=False)]
    return {
        "merged": merged,
        "total_rows": len(combined),
        "exact_duplicate_rows": len(combined) - len(distinct),
        "conflicting_keys": sorted(conflicting[key].unique().tolist()) if key else [],
        "merged_rows": len(merged),
    }


def conflict_details(frames, names, key, conflicting_keys) -> list[dict]:
    """For the first conflicts: every version of the row, and which file wins (the newest)."""
    details = []
    for value in conflicting_keys[:MAX_CONFLICTS_SHOWN]:
        versions = []
        for name, frame in zip(names, frames, strict=True):
            rows = frame[frame[key] == value]
            if not rows.empty:
                versions.append({"file": name, "row": jsonable(rows.head(1))[0]})
        details.append(
            {
                "key": jsonable(pd.DataFrame({key: [value]}))[0][key],
                "versions": versions,
                "winner": versions[-1]["file"],
            }
        )
    return details


def output_name(assets: list[Asset]) -> str:
    """`<shared name>_merged_<date>.<ext>`, named after what the files have in common."""
    stems = [Path(a.name).stem for a in assets]
    shared = os.path.commonprefix([s.lower() for s in stems]).rstrip(" _-(")
    base = stems[-1][: len(shared)] if shared else stems[-1]
    extension = Path(assets[-1].name).suffix
    return f"{base}_merged_{datetime.now(UTC).date().isoformat()}{extension}"


# ---------------------------------------------------------------- finding candidates


def already_proposed(db: Session, org_id: int) -> set[tuple[int, ...]]:
    """File sets with a merge proposal that isn't failed: never propose the same set twice."""
    proposals = db.scalars(
        select(Proposal).where(
            Proposal.org_id == org_id, Proposal.type == "merge", Proposal.status != "failed"
        )
    )
    return {tuple(p.payload["asset_ids"]) for p in proposals}


def find_merge_candidates() -> list[int]:
    """The daily job. Proposes merges for groups of copies. Returns the new proposal ids."""
    created = []
    with system_session() as db:
        sources = db.scalars(select(Source).where(Source.type == "files")).all()
        for source in sources:
            created += find_in_source(db, source)
        db.commit()
    return created


def find_in_source(db: Session, source: Source) -> list[int]:
    assets = [
        asset
        for asset in db.scalars(
            select(Asset).where(Asset.source_id == source.id, Asset.archived.is_(False))
        )
        if asset.name.lower().endswith(TABULAR)
    ]
    # 1. Same columns, compared lowercased and trimmed.
    by_columns = defaultdict(list)
    for asset in assets:
        columns = column_names(db, asset)
        if columns:
            by_columns[columns].append(asset)

    proposed = already_proposed(db, source.org_id)
    created = []
    for same_columns in by_columns.values():
        if len(same_columns) < 2:
            continue
        frames = {a.id: load(file_path(source, a)) for a in same_columns}

        # ...and similar names or many shared rows. Copies of copies join one group.
        def look_like_copies(a: Asset, b: Asset, frames: dict = frames) -> bool:
            return (
                name_similarity(a, b) >= MIN_NAME_SIMILARITY
                or row_overlap(frames[a.id], frames[b.id]) >= MIN_ROW_OVERLAP
            )

        for group in connected_groups(same_columns, look_like_copies):
            asset_ids = tuple(sorted(a.id for a in group))
            if len(group) < 2 or asset_ids in proposed:
                continue
            proposal = propose(db, source, group, frames)
            created.append(proposal.id)
    return created


def propose(db: Session, source: Source, group: list[Asset], frames: dict) -> Proposal:
    group = sorted(group, key=lambda a: (a.last_modified_at, a.name))  # oldest first
    # The oldest file's column order and spelling. execute_merge lines them up the same way.
    ordered = line_up([frames[a.id] for a in group])
    key = pick_key(ordered)
    result = merge_frames(ordered, key)
    names = [a.path for a in group]
    payload = {
        "asset_ids": sorted(a.id for a in group),
        "source_id": source.id,
        "files": [
            {
                "asset_id": a.id,
                "path": a.path,
                "rows": len(frame),
                "last_modified_at": a.last_modified_at.isoformat(),
                # execute_merge refuses to run if a file changed after this was proposed.
                "content_hash": a.content_hash,
            }
            for a, frame in zip(group, ordered, strict=True)
        ],
        "key_column": key,
        "total_rows": result["total_rows"],
        "exact_duplicate_rows": result["exact_duplicate_rows"],
        "conflicting_keys": len(result["conflicting_keys"]),
        "conflicts": conflict_details(ordered, names, key, result["conflicting_keys"])
        if key
        else [],
        "merged_rows": result["merged_rows"],
        "output_name": output_name(group),
    }
    proposal = Proposal(org_id=source.org_id, type="merge", payload=payload)
    db.add(proposal)
    db.flush()
    return proposal


def column_names(db: Session, asset: Asset) -> frozenset[str]:
    names = db.scalars(select(AssetColumn.name).where(AssetColumn.asset_id == asset.id))
    return frozenset(name.strip().lower() for name in names)


def propose_merge(db: Session, user: User, asset_ids: list[int]) -> tuple[dict, list[int]]:
    """A merge proposal for files a person picked (the MCP tool). It only proposes: nothing
    happens to the files until a reviewer approves it on the Approvals page."""
    assets = db.scalars(
        select(Asset).where(
            Asset.id.in_(asset_ids),
            Asset.id.in_(visible_asset_ids(user)),
            Asset.archived.is_(False),
        )
    ).all()
    if len(assets) < 2 or len(assets) != len(set(asset_ids)):
        return {"error": "Give the ids of two or more files you have access to."}, []
    source = db.get(Source, assets[0].source_id)
    if (
        source.type != "files"
        or any(asset.source_id != source.id for asset in assets)
        or not all(asset.name.lower().endswith(TABULAR) for asset in assets)
    ):
        return {"error": "Only csv and xlsx files from the same file source can be merged."}, []
    columns = column_names(db, assets[0])
    if not columns or any(column_names(db, asset) != columns for asset in assets):
        return {"error": "These files don't have the same columns, so they can't be merged."}, []
    if tuple(sorted(a.id for a in assets)) in already_proposed(db, source.org_id):
        return {"error": "A merge of exactly these files has already been proposed."}, []

    # ponytail: the files are read here, inside the request, to build the preview. Fine for
    # spreadsheet-sized files; queue a worker job instead if files get big.
    frames = {a.id: load(file_path(source, a)) for a in assets}
    proposal = propose(db, source, assets, frames)
    preview = {
        key: proposal.payload[key]
        for key in ["total_rows", "exact_duplicate_rows", "conflicting_keys", "merged_rows"]
    }
    result = {
        "proposal_id": proposal.id,
        "status": "pending",
        "files": [file["path"] for file in proposal.payload["files"]],
        **preview,
        "output_name": proposal.payload["output_name"],
        "note": "Nothing has been merged. An admin or the files' owner has to approve it.",
    }
    return result, [a.id for a in assets]


# ---------------------------------------------------------------- executing and undoing


def archive_path(path: Path) -> Path:
    """Where a file goes in its folder's `_archive/`, without overwriting anything there."""
    target = path.parent / ARCHIVE_FOLDER / path.name
    counter = 1
    while target.exists():
        target = path.parent / ARCHIVE_FOLDER / f"{path.stem} ({counter}){path.suffix}"
        counter += 1
    return target


def move(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(source, target)  # a rename on the same disk: the modified time is kept


def write_frame(frame: pd.DataFrame, path: Path):
    if path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
    else:
        frame.to_excel(path, index=False)


def group_names(db: Session, asset_id: int) -> set[str]:
    return set(
        db.scalars(
            select(Group.name)
            .join(AssetPermission, AssetPermission.group_id == Group.id)
            .where(AssetPermission.asset_id == asset_id)
        )
    )


def execute_merge(proposal_id: int):
    """Runs an approved merge. Marks the proposal done, or failed with the reason."""
    with system_session() as db:
        proposal = db.get(Proposal, proposal_id)
        if proposal.status != "approved":
            return
        approver = db.get(User, proposal.reviewed_by)
        source = db.get(Source, proposal.payload["source_id"])
        try:
            result = merge_files(db, proposal, source, approver)
        except Exception as error:
            db.rollback()
            proposal = db.get(Proposal, proposal_id)
            proposal.status = "failed"
            proposal.payload = proposal.payload | {"error": str(error)}
            audit(
                db,
                approver,
                "worker",
                "merge_failed",
                f"proposal:{proposal.id}",
                {"error": str(error)},
            )
            db.commit()
            return
        proposal.status = "done"
        proposal.payload = proposal.payload | result
        audit(db, approver, "worker", "merge_done", f"proposal:{proposal.id}", result)
        db.commit()

    # Outside the transaction above: sync and scan open their own sessions.
    finish_merge(proposal_id)


def merge_files(db: Session, proposal: Proposal, source: Source, approver: User) -> dict:
    payload = proposal.payload
    files = payload["files"]
    assets = [db.get(Asset, f["asset_id"]) for f in files]
    for asset, file in zip(assets, files, strict=True):
        if asset.archived or asset.content_hash != file["content_hash"]:
            raise ValueError(f"{asset.path} changed since the merge was proposed.")

    paths = [file_path(source, asset) for asset in assets]
    frames = line_up([load(path) for path in paths])
    merged = merge_frames(frames, payload["key_column"])["merged"]

    output = paths[-1].parent / payload["output_name"]  # next to the newest file
    if output.exists():
        raise ValueError(f"{output.name} already exists; not overwriting it.")
    write_frame(merged, output)
    audit(
        db,
        approver,
        "worker",
        "merge_file_written",
        f"proposal:{proposal.id}",
        {"path": output.name, "rows": len(merged)},
    )

    moves = []
    try:
        for path in paths:
            target = archive_path(path)
            move(path, target)
            moves.append({"from": str(path), "to": str(target)})
    except OSError:
        # Leave the folder as it was: originals back, the half-done result archived.
        for moved in reversed(moves):
            move(Path(moved["to"]), Path(moved["from"]))
        move(output, archive_path(output))
        raise
    audit(
        db,
        approver,
        "worker",
        "merge_originals_archived",
        f"proposal:{proposal.id}",
        {"moves": moves},
    )

    # The most restrictive: only groups that could see every original see the result.
    allowed = set.intersection(*(group_names(db, asset.id) for asset in assets))
    root = Path(decrypt_config(source.encrypted_config)["path"])
    return {
        "output_path": output.relative_to(root).as_posix(),
        "output_file": str(output),
        "moves": moves,
        "groups": sorted(allowed),
        "executed_at": datetime.now(UTC).isoformat(),
    }


def finish_merge(proposal_id: int):
    """Brings the catalogue in line with the files, then fixes the result's groups and scans it."""
    with system_session() as db:
        proposal = db.get(Proposal, proposal_id)
        source_id = proposal.payload["source_id"]
    # Sync archives the moved originals and catalogues the merged file.
    sync_source(source_id)
    with system_session() as db:
        proposal = db.get(Proposal, proposal_id)
        merged = db.scalar(
            select(Asset).where(
                Asset.source_id == source_id, Asset.path == proposal.payload["output_path"]
            )
        )
        # Sync gave it its folder's groups. Narrow them to the intersection. The next sync
        # leaves them alone while the file is unchanged.
        # ponytail: if someone edits the merged file, sync resets it to the folder's groups.
        save_permissions(db, merged, proposal.payload["groups"])
        proposal.payload = proposal.payload | {"output_asset_id": merged.id}
        db.commit()
        merged_id = merged.id
    scan_asset(merged_id)


def undo_merge(proposal_id: int, user_id: int):
    """Puts the originals back and moves the merged file into `_archive/`. Deletes nothing."""
    with system_session() as db:
        proposal = db.get(Proposal, proposal_id)
        if proposal.type != "merge" or proposal.status != "done":
            return
        payload = proposal.payload
        requester = db.get(User, user_id)
        for moved in payload["moves"]:
            if Path(moved["from"]).exists():
                raise ValueError(f"{moved['from']} exists again; not overwriting it.")
        for moved in payload["moves"]:
            move(Path(moved["to"]), Path(moved["from"]))
        output = Path(payload["output_file"])
        archived_output = archive_path(output)
        if output.exists():
            move(output, archived_output)
        proposal.status = "undone"
        proposal.payload = payload | {
            "undone_at": datetime.now(UTC).isoformat(),
            "archived_output": str(archived_output),
        }
        audit(
            db,
            requester,
            "worker",
            "merge_undone",
            f"proposal:{proposal.id}",
            {"restored": [m["from"] for m in payload["moves"]]},
        )
        db.commit()
        source_id = payload["source_id"]
    # The originals come back under their old paths, so they keep their asset ids, tags and
    # descriptions; the merged file's asset is archived.
    sync_source(source_id)
