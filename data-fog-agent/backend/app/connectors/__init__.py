"""Connectors read a source and describe its assets.

Each connector has `list_assets(config, cursor) -> (changed_assets, new_cursor)`.

- `cursor` maps asset path -> fingerprint from the previous sync ({} the first time).
- `new_cursor` has an entry for every asset that exists now, so sync can archive the rest.
- `changed_assets` are only the assets whose fingerprint changed. Each is a dict with:
  type, name, path, columns [{name, data_type}], row_count, size_bytes,
  last_modified_at, content_hash, groups [group names], and for Slack channels `snippets`.
"""
