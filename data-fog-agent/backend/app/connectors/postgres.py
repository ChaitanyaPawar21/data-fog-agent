"""Postgres connector. An asset is a table or view in the allowed schemas.

Config: {"dsn": "...", "schemas": ["public"], "groups": ["sales"]}
The dsn should use a role with read-only grants. Every connection here is read-only too.
"""

import hashlib
import json

import psycopg
from psycopg import sql

TABLES_QUERY = """
    SELECT t.table_schema, t.table_name, t.table_type, c.reltuples::bigint,
           s.n_tup_ins + s.n_tup_upd + s.n_tup_del AS row_changes,
           greatest(s.last_vacuum, s.last_autovacuum, s.last_analyze, s.last_autoanalyze)
    FROM information_schema.tables t
    JOIN pg_namespace n ON n.nspname = t.table_schema
    JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = t.table_name
    LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
    WHERE t.table_schema = ANY(%s)
    ORDER BY t.table_schema, t.table_name
"""

COLUMNS_QUERY = """
    SELECT table_schema, table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = ANY(%s)
    ORDER BY table_schema, table_name, ordinal_position
"""

DATE_TYPES = {"date", "timestamp without time zone", "timestamp with time zone"}


def list_assets(config: dict, cursor: dict) -> tuple[list[dict], dict]:
    schemas = config.get("schemas", ["public"])
    changed, new_cursor = [], {}

    with psycopg.connect(config["dsn"]) as db:
        db.read_only = True
        columns_by_table = {}
        for schema, table, column, data_type in db.execute(COLUMNS_QUERY, [schemas]):
            columns_by_table.setdefault((schema, table), []).append(
                {"name": column, "data_type": data_type}
            )

        for schema, table, table_type, reltuples, row_changes, last_activity in db.execute(
            TABLES_QUERY, [schemas]
        ).fetchall():
            path = f"{db.info.dbname}.{schema}.{table}"
            columns = columns_by_table.get((schema, table), [])
            # Postgres counts every inserted, updated and deleted row, so if the count and the
            # columns are the same as last time, the table hasn't changed.
            fingerprint = hashlib.sha256(json.dumps([columns, row_changes]).encode()).hexdigest()
            new_cursor[path] = fingerprint
            if cursor.get(path) == fingerprint:
                continue

            is_view = table_type == "VIEW"
            changed.append(
                {
                    "type": "table",
                    "name": table,
                    "path": path,
                    "columns": columns,
                    # reltuples is -1 until the table has been analyzed.
                    "row_count": None if is_view or reltuples < 0 else reltuples,
                    "size_bytes": None,
                    "last_modified_at": newest_date(db, schema, table, columns) or last_activity,
                    "content_hash": fingerprint,
                    "groups": config.get("groups", []),
                }
            )
    return changed, new_cursor


def newest_date(db, schema: str, table: str, columns: list[dict]):
    """The newest date stored in the table.

    Postgres keeps no "last modified" time for a table. Vacuum and analyze times change even
    when the data doesn't, so the newest date in the data is a better freshness signal.
    """
    date_columns = [c["name"] for c in columns if c["data_type"] in DATE_TYPES]
    if not date_columns:
        return None
    # ponytail: max() scans the whole table when the column has no index. Fine for catalogue
    # syncs of normal tables; sample or skip huge tables if syncs get slow.
    query = sql.SQL("SELECT greatest({}) FROM {}").format(
        sql.SQL(", ").join(
            sql.SQL("max({})::timestamptz").format(sql.Identifier(name)) for name in date_columns
        ),
        sql.Identifier(schema, table),
    )
    return db.execute(query).fetchone()[0]


def sample_values(config: dict, path: str, column_names: list[str], limit: int = 200) -> dict:
    """Up to `limit` random non-null values per column, as text.

    Only for PII scanning and doc generation. Callers must never store what this returns.
    """
    _, schema, table = path.split(".", 2)
    samples = {}
    with psycopg.connect(config["dsn"]) as db:
        db.read_only = True
        for name in column_names:
            # ponytail: ORDER BY random() reads the whole table. Use TABLESAMPLE for huge ones.
            query = sql.SQL(
                "SELECT {column}::text FROM {table} WHERE {column} IS NOT NULL "
                "ORDER BY random() LIMIT %s"
            ).format(column=sql.Identifier(name), table=sql.Identifier(schema, table))
            samples[name] = [row[0] for row in db.execute(query, [limit])]
    return samples
