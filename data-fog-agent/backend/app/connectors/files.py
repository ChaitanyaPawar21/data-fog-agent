"""Files connector. Scans a local folder that stands in for Google Drive.

Config: {"path": "/demo/output/drive", "folder_groups": {"sales": ["sales"], "hr": ["hr"]}}
A file's groups come from its top-level folder. Files outside a mapped folder are admin-only.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".pdf", ".txt"}
# Merged-away originals are moved here. They are archived, not part of the catalogue.
ARCHIVE_FOLDER = "_archive"


def list_assets(config: dict, cursor: dict) -> tuple[list[dict], dict]:
    root = Path(config["path"])
    changed, new_cursor = [], {}

    for file in sorted(root.rglob("*")):
        relative = file.relative_to(root)
        if (
            not file.is_file()
            or file.suffix.lower() not in SUPPORTED_EXTENSIONS
            or ARCHIVE_FOLDER in relative.parts
        ):
            continue

        path = relative.as_posix()
        stat = file.stat()
        previous = cursor.get(path)
        # Same size and modified time: skip without reading the file.
        if previous and previous["mtime"] == stat.st_mtime and previous["size"] == stat.st_size:
            new_cursor[path] = previous
            continue

        content_hash = hashlib.sha256(file.read_bytes()).hexdigest()
        new_cursor[path] = {"mtime": stat.st_mtime, "size": stat.st_size, "hash": content_hash}
        # Touched but not edited.
        if previous and previous["hash"] == content_hash:
            continue

        top_folder = relative.parts[0] if len(relative.parts) > 1 else None
        changed.append(
            {
                "type": "file",
                "name": file.name,
                "path": path,
                **read_table_shape(file),
                "size_bytes": stat.st_size,
                "last_modified_at": datetime.fromtimestamp(stat.st_mtime, UTC),
                "content_hash": content_hash,
                "groups": config.get("folder_groups", {}).get(top_folder, []),
            }
        )
    return changed, new_cursor


def read_frame(file: Path, **options) -> pd.DataFrame | None:
    """The file as a table, or None for files that aren't tabular."""
    suffix = file.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(file, **options)
    if suffix == ".xlsx":
        # ponytail: only the first sheet is read. Make each sheet an asset if that matters.
        return pd.read_excel(file, **options)
    return None


def read_table_shape(file: Path) -> dict:
    """Headers become columns for tabular files. Other files have no columns."""
    frame = read_frame(file)
    if frame is None:
        return {"columns": [], "row_count": None}

    return {
        "columns": [
            {"name": str(name), "data_type": str(dtype)} for name, dtype in frame.dtypes.items()
        ],
        "row_count": len(frame),
    }


def sample_values(config: dict, path: str, column_names: list[str], limit: int = 200) -> dict:
    """Up to `limit` random non-empty values per column, as text.

    Only for PII scanning and doc generation. Callers must never store what this returns.
    """
    # Read as text, so a phone number column with gaps doesn't turn into 9876543210.0.
    frame = read_frame(Path(config["path"]) / path, dtype=str)
    if frame is None:
        return {}
    frame.columns = [str(name) for name in frame.columns]
    return {
        name: frame[name].dropna().sample(frac=1, random_state=0).head(limit).tolist()
        for name in column_names
        if name in frame
    }
