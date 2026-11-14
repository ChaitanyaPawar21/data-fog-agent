"""Slack export connector. Reads a standard export folder: channels.json, users.json and
one JSON file per channel per day. Each channel is an asset and its messages are snippets.

Config: {"path": "/demo/output/slack", "channel_groups": {"sales": ["sales"]}}
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def list_assets(config: dict, cursor: dict) -> tuple[list[dict], dict]:
    root = Path(config["path"])
    users = json.loads((root / "users.json").read_text())
    user_names = {user["id"]: user.get("real_name") or user["name"] for user in users}
    changed, new_cursor = [], {}

    for channel in json.loads((root / "channels.json").read_text()):
        name = channel["name"]
        day_files = sorted((root / name).glob("*.json"))
        content = b"".join(day_file.read_bytes() for day_file in day_files)
        content_hash = hashlib.sha256(content).hexdigest()
        new_cursor[name] = content_hash
        if cursor.get(name) == content_hash:
            continue

        snippets = [
            {
                "text": message["text"],
                "author": user_names.get(message.get("user")),
                "posted_at": datetime.fromtimestamp(float(message["ts"]), UTC),
            }
            for day_file in day_files
            for message in json.loads(day_file.read_text())
            # Subtypes are joins, leaves, bot posts and similar, not people talking.
            if message.get("type") == "message" and "subtype" not in message and message["text"]
        ]
        changed.append(
            {
                "type": "channel",
                "name": name,
                "path": name,
                "columns": [],
                "row_count": len(snippets),
                "size_bytes": len(content),
                "last_modified_at": max((s["posted_at"] for s in snippets), default=None),
                "content_hash": content_hash,
                "groups": config.get("channel_groups", {}).get(name, []),
                "snippets": snippets,
            }
        )
    return changed, new_cursor
