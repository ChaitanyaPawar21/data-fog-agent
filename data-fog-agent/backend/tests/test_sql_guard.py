import generate
import psycopg
import pytest
import sqlglot
from psycopg.conninfo import make_conninfo

from app import sql_guard
from app.pii import PHONE
from app.sql_guard import SqlRejected, check_sql, masked_output_names, run_select

VISIBLE = {"public.orders_v2", "public.customers_new", "public.tickets"}


def reader_dsn(database: str) -> str:
    return make_conninfo(
        generate.ADMIN_DATABASE_URL,
        user=generate.READER_ROLE,
        password=generate.READER_PASSWORD,
        dbname=database,
    )


def checked_sql(sql: str) -> str:
    query, _ = check_sql(sql, VISIBLE)
    return query.sql(dialect="postgres")


def test_a_plain_select_gets_a_row_limit():
    assert checked_sql("SELECT count(*) FROM orders_v2").endswith("LIMIT 200")
    assert checked_sql("SELECT * FROM public.orders_v2 LIMIT 10").endswith("LIMIT 10")
    assert checked_sql("SELECT * FROM orders_v2 LIMIT 5000").endswith("LIMIT 200")


def test_everyday_analysis_sql_is_allowed():
    query, tables = check_sql(
        """
        WITH monthly AS (
            SELECT date_trunc('month', order_date) AS month, sum(amount) / 100.0 AS rupees
            FROM orders_v2 o JOIN customers_new c ON c.customer_id = o.customer_id
            WHERE status = 'completed' AND order_date >= DATE '2026-01-01'
            GROUP BY 1
        )
        SELECT to_char(month, 'YYYY-MM'), round(rupees, 2), age(month) FROM monthly
        ORDER BY 1
        """,
        VISIBLE,
    )
    assert tables == {"public.orders_v2", "public.customers_new"}


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders_v2",
        "UPDATE orders_v2 SET amount = 0",
        "INSERT INTO orders_v2 (order_id) VALUES (1)",
        "DROP TABLE orders_v2",
        "CREATE TABLE stolen AS SELECT * FROM customers_new",
        "TRUNCATE orders_v2",
        "ALTER TABLE orders_v2 ADD COLUMN x int",
        "GRANT SELECT ON orders_v2 TO public",
        "COPY orders_v2 TO '/tmp/orders.csv'",
        "SELECT 1; DELETE FROM orders_v2",
        "WITH gone AS (DELETE FROM orders_v2 RETURNING *) SELECT * FROM gone",
        "SELECT * INTO stolen FROM customers_new",
        "SELECT * FROM orders_v2 FOR UPDATE",
        "EXPLAIN ANALYZE SELECT * FROM orders_v2",
        "SET statement_timeout = 0",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM query_to_xml('select * from employees', true, true, '')",
        "SELECT set_config('default_transaction_read_only', 'off', false)",
        "SELECT pg_sleep(60)",
        "SELECT dblink('host=elsewhere', 'select 1')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT * FROM refunds",
        "SELECT * FROM finance.revenue_monthly",
        "SELECT * FROM pg_catalog.pg_user",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM orders_v2 WHERE customer_id IN (SELECT col_1 FROM tbl_cst_mst)",
        "this is not sql",
        "",
    ],
)
def test_anything_but_one_safe_select_is_rejected(sql):
    with pytest.raises(SqlRejected):
        check_sql(sql, VISIBLE)


def test_a_cte_name_is_not_mistaken_for_a_hidden_table():
    checked_sql("WITH refunds AS (SELECT 1 AS x) SELECT x FROM refunds")


def test_pii_columns_stay_masked_through_aliases_and_subqueries():
    query, _ = check_sql(
        """
        SELECT contact, upper(email) AS mail, city FROM (
            SELECT phone AS contact, email, city FROM customers_new
        ) AS inner_query
        """,
        VISIBLE,
    )
    masked = masked_output_names(query, {"phone", "email"})
    assert {"contact", "mail"} <= masked
    assert "city" not in masked


def test_results_mask_tagged_columns_and_pii_in_free_text(demo):
    query, _ = check_sql("SELECT phone AS contact, city FROM customers_new", VISIBLE)
    result = run_select(reader_dsn("sales_db"), query, {"phone"})
    assert result["columns"] == ["contact", "city"]
    assert all(row[0].startswith("XXXX") for row in result["rows"])

    # tickets.notes isn't tagged yet (that's the PII scanner's job), so the text net catches it.
    query, _ = check_sql("SELECT notes FROM tickets", VISIBLE)
    notes = [row[0] for row in run_select(reader_dsn("support_db"), query, set())["rows"]]
    assert notes and not any(PHONE.search(note) for note in notes if note)


def test_the_database_itself_refuses_writes_and_slow_queries(demo, monkeypatch):
    # These skip check_sql on purpose: the read-only transaction is the layer underneath.
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        run_select(reader_dsn("sales_db"), sqlglot.parse_one("CREATE TABLE x (a int)"), set())

    monkeypatch.setattr(sql_guard, "TIMEOUT", "100ms")
    with pytest.raises(psycopg.errors.QueryCanceled):
        run_select(reader_dsn("sales_db"), sqlglot.parse_one("SELECT pg_sleep(2)"), set())
