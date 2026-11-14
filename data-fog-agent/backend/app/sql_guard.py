"""Checks and runs SQL the agent writes against a connected Postgres source.

Every query goes through `check_sql` first. It allows one plain SELECT over tables the
user can see, and nothing else. Running it then adds a read-only transaction and a
timeout, so a query that slips past the checks still can't change or hang anything.
"""

import psycopg
import sqlglot
from sqlglot import exp

from app.pii import mask_pii_in_text, mask_value

MAX_ROWS = 200
TIMEOUT = "10s"

# Functions sqlglot doesn't recognise come through as Anonymous. That's where the
# dangerous ones live (pg_read_file, query_to_xml, set_config, dblink, pg_sleep...),
# so unknown functions are refused unless they're listed here.
ALLOWED_UNKNOWN_FUNCTIONS = {"age", "split_part", "initcap"}

WRITES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Into, exp.Lock)


class SqlRejected(Exception):
    pass


def check_sql(sql: str, visible_tables: set[str]) -> tuple[exp.Select, set[str]]:
    """Returns the parsed query with a LIMIT and the tables it reads.

    Raises SqlRejected with a reason the agent can act on.

    `visible_tables` holds "schema.table" names the user may read in this database.
    """
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as error:
        raise SqlRejected(f"Could not parse the SQL: {error}") from None
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlRejected("Send exactly one statement.")
    query = statements[0]
    if not isinstance(query, exp.Select):
        raise SqlRejected("Only SELECT queries are allowed.")

    for node in query.walk():
        if isinstance(node, WRITES):
            raise SqlRejected("Only reading is allowed: no writes, SELECT INTO or row locks.")
        if isinstance(node, exp.Anonymous) and node.name.lower() not in ALLOWED_UNKNOWN_FUNCTIONS:
            raise SqlRejected(f"The function {node.name}() is not allowed.")

    cte_names = {cte.alias for cte in query.find_all(exp.CTE)}
    used_tables = set()
    for table in query.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue  # a function in FROM, like generate_series(); checked above
        if not table.db and table.name in cte_names:
            continue
        name = f"{table.db or 'public'}.{table.name}"
        if name not in visible_tables:
            raise SqlRejected(f"Table {name} doesn't exist or you don't have access to it.")
        used_tables.add(name)

    limit = query.args.get("limit")
    current = limit.expression if limit else None
    if not (isinstance(current, exp.Literal) and current.is_int and int(current.name) <= MAX_ROWS):
        query = query.limit(MAX_ROWS)
    return query, used_tables


def masked_output_names(query: exp.Select, pii_columns: set[str]) -> set[str]:
    """Names of result columns that may carry a PII column's values, even under an alias.

    ponytail: matches by column name across every SELECT in the query until nothing new
    turns up. It over-masks when unrelated tables share a column name, which is the safe
    direction. Use sqlglot's lineage if over-masking starts hiding useful columns.
    """
    tainted = set(pii_columns)
    while True:
        found = set()
        for select in query.find_all(exp.Select):
            for projection in select.expressions:
                if isinstance(projection, exp.Star):
                    continue  # SELECT * keeps the original names, which are already tainted
                if any(column.name in tainted for column in projection.find_all(exp.Column)):
                    found.add(projection.alias_or_name)
        if found <= tainted:
            return tainted
        tainted |= found


def mask_cell(value, mask_whole: bool):
    if value is None:
        return None
    if mask_whole:
        return mask_value(str(value))
    if isinstance(value, str):
        # A second net for PII in columns nobody has tagged yet, like free-text notes.
        return mask_pii_in_text(value)
    if isinstance(value, int | float | bool):
        return value
    return str(value)  # dates, decimals: JSON-friendly text


def run_select(dsn: str, query: exp.Select, pii_columns: set[str]) -> dict:
    """Runs a query that passed check_sql. Returns {"columns": [...], "rows": [[...]]}."""
    with psycopg.connect(dsn) as db:
        db.read_only = True
        db.execute(f"SET LOCAL statement_timeout = '{TIMEOUT}'")
        cursor = db.execute(query.sql(dialect="postgres"))
        columns = [column.name for column in cursor.description]
        rows = cursor.fetchmany(MAX_ROWS)
        db.rollback()

    masked = masked_output_names(query, pii_columns)
    return {
        "columns": columns,
        "rows": [
            [mask_cell(value, name in masked) for name, value in zip(columns, row, strict=True)]
            for row in rows
        ],
    }
