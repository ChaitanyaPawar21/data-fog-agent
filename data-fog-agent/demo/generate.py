"""Build the foggy demo company "Chaiwala Co.".

Creates three Postgres databases, a folder of files standing in for Google Drive,
a Slack export, and the app's users and groups. Writes ground_truth.yaml with every
planted problem and the correct answers, for tests and evals.

Run inside the backend container:
    docker compose exec backend uv run python /demo/generate.py

Everything is rebuilt on each run. The data is the same every time (fixed seed),
except file modified times, which are set relative to today.
"""

import bisect
import json
import os
import random
import shutil
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import psycopg
import yaml
from faker import Faker
from fpdf import FPDF
from psycopg.conninfo import make_conninfo
from sqlalchemy import select

from app.auth import hash_password
from app.db import system_session
from app.models import Group, Org, Source, User, UserGroup
from app.pii import verhoeff_check_digit
from app.sync import create_source

DEMO_DIR = Path(__file__).parent
ORG_NAME = "Chaiwala Co."
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "chaiwala-demo")

# A superuser is needed to create databases and roles.
ADMIN_DATABASE_URL = os.environ.get(
    "DEMO_ADMIN_DATABASE_URL", "postgresql://postgres:postgres@db:5432/postgres"
)
# Connectors read the demo databases with this role. It can only SELECT.
READER_ROLE = "datafog_reader"
READER_PASSWORD = "reader"

ORDERS_START = date(2025, 1, 1)
ORDERS_END = date(2026, 10, 31)
# The old `orders` table stopped getting new rows when orders_v2 went live.
OLD_ORDERS_CUTOFF = date(2025, 12, 31)
OLD_CUSTOMERS_CUTOFF = date(2025, 6, 30)

PRODUCTS = {  # sku -> price in paise
    "MASALA-CHAI-250G": 34900,
    "ELAICHI-CHAI-250G": 36900,
    "GINGER-CHAI-250G": 32900,
    "ASSAM-GOLD-500G": 54900,
    "DARJEELING-100G": 44900,
    "CHAI-MUG": 29900,
    "GIFT-BOX": 129900,
}
RETAIL_STORES = ["STORE-BLR-01", "STORE-MUM-01", "STORE-DEL-01", "STORE-PUN-01"]

GROUPS = ["sales", "finance", "hr", "support"]
PEOPLE = [
    {"email": "admin@chaiwala.co", "name": "Admin", "role": "admin", "groups": []},
    {"email": "priya.sharma@chaiwala.co", "name": "Priya Sharma", "groups": ["sales"]},
    {"email": "rahul.verma@chaiwala.co", "name": "Rahul Verma", "groups": ["sales"]},
    {"email": "anita.desai@chaiwala.co", "name": "Anita Desai", "groups": ["finance"]},
    {"email": "vikram.rao@chaiwala.co", "name": "Vikram Rao", "groups": ["finance"]},
    {"email": "meera.iyer@chaiwala.co", "name": "Meera Iyer", "groups": ["hr"]},
    {"email": "arjun.nair@chaiwala.co", "name": "Arjun Nair", "groups": ["support"]},
    {"email": "kavya.reddy@chaiwala.co", "name": "Kavya Reddy", "groups": ["support"]},
]


# ---------------------------------------------------------------- personal data


def fake_aadhaar() -> str:
    # Real Aadhaar numbers never start with 0 or 1 and end in a Verhoeff check digit.
    payload = str(random.randint(2, 9)) + "".join(random.choices("0123456789", k=10))
    number = payload + str(verhoeff_check_digit(payload))
    return f"{number[:4]} {number[4:8]} {number[8:]}"


def fake_pan() -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    # The 4th letter is P for an individual.
    return (
        "".join(random.choices(letters, k=3))
        + "P"
        + random.choice(letters)
        + f"{random.randint(0, 9999):04d}"
        + random.choice(letters)
    )


def fake_mobile() -> str:
    return random.choice("6789") + f"{random.randint(0, 999_999_999):09d}"


def random_date(start: date, end: date) -> date:
    return start + timedelta(days=random.randint(0, (end - start).days))


def random_datetime(start: date, end: date) -> datetime:
    day = random_date(start, end)
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(
        seconds=random.randint(0, 86_399)
    )


# ---------------------------------------------------------------- business data


def make_customers(fake: Faker) -> list[dict]:
    signup_dates = sorted(random_date(date(2022, 1, 1), ORDERS_END) for _ in range(300))
    customers = []
    for customer_id, signup_date in enumerate(signup_dates, start=1):
        first, last = fake.first_name(), fake.last_name()
        domain = random.choice(["gmail.com", "yahoo.co.in", "outlook.com", "rediffmail.com"])
        customers.append(
            {
                "customer_id": customer_id,
                "name": f"{first} {last}",
                "email": f"{first}.{last}{customer_id}@{domain}".lower(),
                "phone": fake_mobile(),
                "city": fake.city(),
                "address": fake.address().replace("\n", ", "),
                "pan": fake_pan(),
                "signup_date": signup_date,
            }
        )
    return customers


def make_orders(customers: list[dict]) -> tuple[list[dict], list[dict]]:
    """Orders with amounts in paise, plus their line items."""
    signup_dates = [customer["signup_date"] for customer in customers]
    order_dates = sorted(random_date(ORDERS_START, ORDERS_END) for _ in range(3000))
    orders, lines = [], []
    for order_id, order_date in enumerate(order_dates, start=1):
        # Only customers who had signed up by the order date can place it.
        signed_up = bisect.bisect_right(signup_dates, order_date)
        customer = customers[random.randrange(signed_up)]
        channel = random.choice(["online", "online", "retail"])

        amount = 0
        for line_no in range(1, random.randint(1, 3) + 1):
            sku = random.choice(list(PRODUCTS))
            qty = random.randint(1, 4)
            amount += qty * PRODUCTS[sku]
            lines.append(
                {
                    "order_id": order_id,
                    "line_no": line_no,
                    "sku": sku,
                    "qty": qty,
                    "unit_price": PRODUCTS[sku],
                }
            )

        orders.append(
            {
                "order_id": order_id,
                "customer_id": customer["customer_id"],
                "order_date": order_date,
                "channel": channel,
                "store_code": "ONLINE" if channel == "online" else random.choice(RETAIL_STORES),
                "amount": amount,
                "status": random.choices(["completed", "returned", "cancelled"], [85, 8, 7])[0],
            }
        )
    return orders, lines


def make_monthly_revenue(orders: list[dict]) -> list[dict]:
    """Finance's view: gross counts completed and returned orders, net takes returns off."""
    months = {}
    for order in orders:
        if order["status"] == "cancelled":
            continue
        month = order["order_date"].replace(day=1)
        totals = months.setdefault(month, {"gross": 0, "returns": 0})
        totals["gross"] += order["amount"]
        if order["status"] == "returned":
            totals["returns"] += order["amount"]

    return [
        {
            "month": month,
            "gross_revenue": rupees(totals["gross"]),
            "returns": rupees(totals["returns"]),
            "net_revenue": rupees(totals["gross"] - totals["returns"]),
        }
        for month, totals in sorted(months.items())
    ]


def rupees(paise: int) -> float:
    return round(paise / 100, 2)


def make_ticket_note(customer: dict, pii_counts: dict) -> str:
    roll = random.random()
    if roll < 0.12:
        pii_counts["phone"] += 1
        prefix = random.choice(["", "+91 ", "+91-"])
        return f"Customer asked for a call back on {prefix}{customer['phone']} after 6pm."
    if roll < 0.18:
        pii_counts["aadhaar"] += 1
        return f"KYC for refund done. Customer shared Aadhaar {fake_aadhaar()} over chat."
    return random.choice(
        [
            "Delivery was late by two days. Apologised and shared a coupon.",
            "Package arrived damaged. Replacement dispatched.",
            "Customer wants to change the delivery address before dispatch.",
            "Refund processed to original payment method.",
            "Asked about the difference between Assam Gold and Darjeeling.",
            "Coupon code did not apply at checkout. Applied it manually.",
        ]
    )


def make_tickets(customers: list[dict]) -> tuple[list[dict], dict]:
    pii_counts = {"phone": 0, "aadhaar": 0}
    tickets = []
    for ticket_id in range(1, 401):
        customer = random.choice(customers)
        tickets.append(
            {
                "ticket_id": ticket_id,
                "customer_id": customer["customer_id"],
                "created_at": random_datetime(ORDERS_START, ORDERS_END),
                "subject": random.choice(
                    ["Late delivery", "Damaged item", "Refund", "Product question", "Coupon"]
                ),
                "status": random.choice(["open", "closed", "closed", "closed"]),
                "notes": make_ticket_note(customer, pii_counts),
            }
        )
    return tickets, pii_counts


def make_app_logs(customers: list[dict]) -> list[dict]:
    logs = []
    for log_id in range(1, 1001):
        email = random.choice(customers)["email"]
        roll = random.random()
        if roll < 0.08:
            level, message = "WARN", f"login failed for {email}"
        elif roll < 0.15:
            level, message = "INFO", f"password reset link sent to {email}"
        else:
            path = random.choice(["/api/orders", "/api/cart", "/api/products", "/api/checkout"])
            status = random.choice([200, 200, 200, 201, 404, 500])
            level = "ERROR" if status == 500 else "INFO"
            message = f"GET {path} {status} {random.randint(5, 900)}ms"
        logs.append(
            {
                "log_id": log_id,
                "logged_at": random_datetime(date(2026, 10, 1), ORDERS_END),
                "level": level,
                "message": message,
            }
        )
    return logs


# ---------------------------------------------------------------- databases


def insert_rows(connection, table: str, rows: list[dict]):
    columns = list(rows[0])
    placeholders = ", ".join(["%s"] * len(columns))
    with connection.cursor() as cursor:
        cursor.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            [tuple(row.values()) for row in rows],
        )


def recreate_database(name: str) -> psycopg.Connection:
    with psycopg.connect(ADMIN_DATABASE_URL, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {name}")
        admin.execute(f"GRANT CONNECT ON DATABASE {name} TO {READER_ROLE}")
    return psycopg.connect(make_conninfo(ADMIN_DATABASE_URL, dbname=name))


def create_reader_role():
    with psycopg.connect(ADMIN_DATABASE_URL, autocommit=True) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", [READER_ROLE]
        ).fetchone()
        if not exists:
            admin.execute(f"CREATE ROLE {READER_ROLE} LOGIN PASSWORD '{READER_PASSWORD}'")
        # A second guard on top of SELECT-only grants: every transaction starts read-only.
        admin.execute(f"ALTER ROLE {READER_ROLE} SET default_transaction_read_only = on")


def grant_reader_access(connection):
    # A real database has been analyzed many times, so its row estimates exist.
    connection.execute("ANALYZE")
    connection.execute(f"GRANT USAGE ON SCHEMA public TO {READER_ROLE}")
    connection.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READER_ROLE}")


def build_sales_db(customers, orders, lines):
    with recreate_database("sales_db") as db:
        db.execute("""
            CREATE TABLE customers (
                customer_id int PRIMARY KEY, name text, email text, phone text,
                city text, signup_date date);
            CREATE TABLE customers_new (
                customer_id int PRIMARY KEY, full_name text, email text, phone text,
                city text, address text, signup_date date);
            CREATE TABLE customers_backup_2023 (
                customer_id int, name text, email text, phone text,
                city text, signup_date text);
            CREATE TABLE orders (
                order_id int PRIMARY KEY, customer_id int, order_date date, channel text,
                store_code text, amount numeric(12, 2), status text);
            CREATE TABLE orders_v2 (
                order_id int PRIMARY KEY, customer_id int, order_date date, channel text,
                store_code text, amount bigint, status text);
            CREATE TABLE ord_dtl (
                order_id int, line_no int, sku text, qty int, unit_price bigint,
                PRIMARY KEY (order_id, line_no));
            CREATE TABLE sales_2023 (
                month date, channel text, gross_amount numeric(14, 2));
        """)

        old_customers = [c for c in customers if c["signup_date"] <= OLD_CUSTOMERS_CUTOFF]
        insert_rows(db, "customers", [customer_row(c) for c in old_customers])
        insert_rows(
            db,
            "customers_new",
            [
                {**customer_row(c, name_column="full_name"), "address": c["address"]}
                for c in customers
            ],
        )
        # Someone exported this to a spreadsheet and back, so the dates became text.
        insert_rows(
            db,
            "customers_backup_2023",
            [
                {**customer_row(c), "signup_date": c["signup_date"].strftime("%d/%m/%Y")}
                for c in customers
                if c["signup_date"] < date(2024, 1, 1)
            ],
        )

        insert_rows(db, "orders_v2", orders)
        # The old system kept rupees. orders_v2 keeps paise, in a column with the same name.
        insert_rows(
            db,
            "orders",
            [
                {**o, "amount": rupees(o["amount"])}
                for o in orders
                if o["order_date"] <= OLD_ORDERS_CUTOFF
            ],
        )
        insert_rows(db, "ord_dtl", lines)
        insert_rows(
            db,
            "sales_2023",
            [
                {
                    "month": date(2023, month, 1),
                    "channel": channel,
                    "gross_amount": round(random.uniform(150_000, 450_000), 2),
                }
                for month in range(1, 13)
                for channel in ["online", "retail"]
            ],
        )

        # The metrics policy says 60 days. This view was written earlier and was never updated.
        db.execute("""
            CREATE VIEW active_customers AS
            SELECT DISTINCT customer_id FROM orders_v2
            WHERE status = 'completed' AND order_date >= current_date - 90
        """)
        grant_reader_access(db)


def customer_row(customer: dict, name_column: str = "name") -> dict:
    return {
        "customer_id": customer["customer_id"],
        name_column: customer["name"],
        "email": customer["email"],
        "phone": customer["phone"],
        "city": customer["city"],
        "signup_date": customer["signup_date"],
    }


def build_finance_db(customers, orders, monthly_revenue):
    with recreate_database("finance_db") as db:
        db.execute("""
            CREATE TABLE revenue_monthly (
                month date PRIMARY KEY, gross_revenue numeric(14, 2),
                returns numeric(14, 2), net_revenue numeric(14, 2));
            CREATE TABLE tbl_cst_mst (
                col_1 int PRIMARY KEY, col_2 text, col_3 text, col_4 text,
                col_5 text, col_6 text, col_7 numeric(12, 2));
            CREATE TABLE refunds (
                refund_id int PRIMARY KEY, order_id int, refund_date date,
                amount numeric(12, 2), reason text);
        """)
        insert_rows(db, "revenue_monthly", monthly_revenue)
        # Finance's customer master. The cryptic names hide name, phone, email, city and PAN.
        insert_rows(
            db,
            "tbl_cst_mst",
            [
                {
                    "col_1": c["customer_id"],
                    "col_2": c["name"],
                    "col_3": c["phone"],
                    "col_4": c["email"],
                    "col_5": c["city"],
                    "col_6": c["pan"],
                    "col_7": random.choice([5000, 10000, 25000, 50000]),
                }
                for c in customers
            ],
        )
        returned = [o for o in orders if o["status"] == "returned"]
        insert_rows(
            db,
            "refunds",
            [
                {
                    "refund_id": refund_id,
                    "order_id": o["order_id"],
                    "refund_date": o["order_date"] + timedelta(days=random.randint(3, 15)),
                    "amount": rupees(o["amount"]),
                    "reason": random.choice(["damaged", "wrong item", "not needed", "late"]),
                }
                for refund_id, o in enumerate(returned, start=1)
            ],
        )
        grant_reader_access(db)


def build_support_db(tickets, app_logs):
    with recreate_database("support_db") as db:
        db.execute("""
            CREATE TABLE tickets (
                ticket_id int PRIMARY KEY, customer_id int, created_at timestamptz,
                subject text, status text, notes text);
            CREATE TABLE app_logs (
                log_id int PRIMARY KEY, logged_at timestamptz, level text, message text);
        """)
        insert_rows(db, "tickets", tickets)
        insert_rows(db, "app_logs", app_logs)
        grant_reader_access(db)


# ---------------------------------------------------------------- drive files


def set_modified_days_ago(path: Path, days: int):
    timestamp = time.time() - days * 86_400
    os.utime(path, (timestamp, timestamp))


def write_sales_reports(folder: Path, orders: list[dict]) -> dict:
    """Three versions of one report: overlapping rows and a few edited amounts."""
    recent = [
        o for o in orders if o["order_date"] >= date(2026, 9, 1) and o["status"] != "cancelled"
    ]
    base = pd.DataFrame(
        {
            "order_id": o["order_id"],
            "order_date": o["order_date"],
            "channel": o["channel"],
            "store_code": o["store_code"],
            "amount_rupees": rupees(o["amount"]),
        }
        for o in recent[:140]
    )

    def version(start, end, edits):
        frame = base.iloc[start:end].copy()
        for row, change in edits.items():
            frame.loc[row, "amount_rupees"] = round(frame.loc[row, "amount_rupees"] + change, 2)
        return frame

    # Oldest first. Most recently modified wins a conflict, so this order matters.
    reports = [
        ("sales_report_final.xlsx", version(0, 100, {}), 60),
        ("sales_report_final_v2.xlsx", version(20, 120, {45: 50, 70: -20, 95: 120}), 30),
        ("sales_report_FINAL_use_this.xlsx", version(40, 140, {70: 10, 110: 35}), 5),
    ]
    for name, frame, days_ago in reports:
        frame.to_excel(folder / name, index=False)
        set_modified_days_ago(folder / name, days_ago)

    return expected_merge([(name, frame) for name, frame, _ in reports], key="order_id")


def write_orders_exports(folder: Path, orders: list[dict]) -> dict:
    """The same January export, downloaded twice."""
    january = pd.DataFrame(
        {
            key: o[key]
            for key in ["order_id", "customer_id", "order_date", "channel", "amount", "status"]
        }
        for o in orders
        if date(2026, 1, 1) <= o["order_date"] <= date(2026, 1, 31)
    )
    names = ["orders_export_jan.csv", "orders_export_jan (1).csv"]
    for name in names:
        january.to_csv(folder / name, index=False)
        set_modified_days_ago(folder / name, 280)
    return expected_merge([(name, january) for name in names], key="order_id")


def expected_merge(files: list[tuple[str, pd.DataFrame]], key: str) -> dict:
    """What a correct merge of these files must produce. Files are given oldest first."""
    combined = pd.concat([frame for _, frame in files])
    distinct_rows = combined.drop_duplicates()
    conflicting = distinct_rows[distinct_rows.duplicated(subset=key, keep=False)]
    merged = combined.drop_duplicates(subset=key, keep="last")
    result = {
        "files": [name for name, _ in files],
        "key_column": key,
        "total_rows": len(combined),
        # Rows that are an exact copy of a row already counted.
        "exact_duplicate_rows": len(combined) - len(distinct_rows),
        "conflicting_keys": sorted(int(k) for k in conflicting[key].unique()),
        "merged_rows": len(merged),
    }
    # A checksum for the merged values. The orders export keeps paise, the reports rupees.
    amount_column = "amount_rupees" if "amount_rupees" in merged else "amount"
    result["amount_column"] = amount_column
    result["merged_amount_sum"] = round(float(merged[amount_column].sum()), 2)
    return result


def write_metrics_policy(path: Path):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Chaiwala Co. - Metrics Policy", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    for paragraph in [
        "Owner: Finance team. Approved by the CFO.",
        "Active customer: a customer who has placed at least one completed order "
        "in the last 60 days.",
        "Revenue: all revenue figures reported to leadership are net of returns. "
        "Cancelled orders are excluded.",
        "Targets: the only approved source of 2026 targets is finance/targets_2026.xlsx.",
    ]:
        pdf.multi_cell(0, 7, paragraph, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    pdf.output(str(path))
    set_modified_days_ago(path, 400)


def write_targets(path: Path):
    targets = pd.DataFrame(
        {
            "month": date(2026, month, 1),
            "channel": channel,
            "target_rupees": random.randrange(400_000, 900_000, 10_000),
        }
        for month in range(1, 13)
        for channel in ["online", "retail"]
    )
    targets.to_excel(path, index=False)
    set_modified_days_ago(path, 20)


def write_employee_list(path: Path, fake: Faker) -> list[str]:
    employees = []
    for employee_id in range(1, 26):
        first, last = fake.first_name(), fake.last_name()
        employees.append(
            {
                "employee_id": f"EMP{employee_id:03d}",
                "name": f"{first} {last}",
                "email": f"{first}.{last}@chaiwala.co".lower(),
                "phone": fake_mobile(),
                "pan": fake_pan(),
                "aadhaar": fake_aadhaar(),
                "city": fake.city(),
                "department": random.choice(["Sales", "Finance", "Ops", "Support", "Retail"]),
                "monthly_salary": random.randrange(25_000, 200_000, 1_000),
            }
        )
    pd.DataFrame(employees).to_excel(path, index=False)
    set_modified_days_ago(path, 10)
    return [employee["aadhaar"] for employee in employees]


def build_drive(drive: Path, orders: list[dict], fake: Faker) -> dict:
    for folder in ["sales", "finance", "hr"]:
        (drive / folder).mkdir(parents=True)

    sales_reports_merge = write_sales_reports(drive / "sales", orders)
    orders_exports_merge = write_orders_exports(drive / "sales", orders)
    write_metrics_policy(drive / "finance" / "metrics_policy.pdf")
    write_targets(drive / "finance" / "targets_2026.xlsx")
    employee_aadhaars = write_employee_list(drive / "hr" / "employee_list.xlsx", fake)
    return {
        "merges": [sales_reports_merge, orders_exports_merge],
        "employee_aadhaars": employee_aadhaars,
    }


# ---------------------------------------------------------------- slack export


SLACK_MESSAGES = [
    # channel, day, author email, text
    (
        "data",
        "2026-01-05",
        "vikram.rao@chaiwala.co",
        "Heads up: use orders_v2 not orders. The old orders table stopped updating in Dec 2025.",
    ),
    ("data", "2026-01-05", "rahul.verma@chaiwala.co", "Got it. Is the amount column the same?"),
    (
        "data",
        "2026-01-05",
        "vikram.rao@chaiwala.co",
        "No! amounts in orders_v2 are in paise. Divide by 100 for rupees. orders had rupees.",
    ),
    ("data", "2026-02-11", "anita.desai@chaiwala.co", "Who owns the customer tables?"),
    (
        "data",
        "2026-02-11",
        "vikram.rao@chaiwala.co",
        "Priya owns customer data. customers_new is the current one, ask her before changing it.",
    ),
    (
        "data",
        "2026-03-02",
        "priya.sharma@chaiwala.co",
        "customers_backup_2023 has signup dates stored as text (DD/MM/YYYY). Don't trust it.",
    ),
    (
        "data",
        "2026-04-18",
        "vikram.rao@chaiwala.co",
        "tbl_cst_mst in finance_db is finance's customer master. col_1 is the customer id.",
    ),
    ("data", "2026-06-09", "rahul.verma@chaiwala.co", "Anyone know what ord_dtl is?"),
    (
        "data",
        "2026-06-09",
        "vikram.rao@chaiwala.co",
        "Order line items. unit_price is in paise there too.",
    ),
    (
        "sales",
        "2026-09-20",
        "rahul.verma@chaiwala.co",
        "Which sales report is right? final, final_v2 or FINAL_use_this?",
    ),
    (
        "sales",
        "2026-09-20",
        "priya.sharma@chaiwala.co",
        "Use sales_report_FINAL_use_this, it has the corrected amounts.",
    ),
    (
        "sales",
        "2026-10-02",
        "priya.sharma@chaiwala.co",
        "Targets for 2026 are in finance/targets_2026.xlsx, not in the old deck.",
    ),
    ("sales", "2026-10-14", "rahul.verma@chaiwala.co", "Great Diwali week in Bengaluru store!"),
    (
        "finance",
        "2026-04-03",
        "anita.desai@chaiwala.co",
        "Reminder: finance revenue is net of returns. Sales dashboards show gross.",
    ),
    (
        "finance",
        "2026-04-03",
        "vikram.rao@chaiwala.co",
        "revenue_monthly has gross, returns and net columns if you need both.",
    ),
    (
        "finance",
        "2026-07-21",
        "anita.desai@chaiwala.co",
        "Per the metrics policy an active customer ordered in the last 60 days.",
    ),
    (
        "finance",
        "2026-07-21",
        "vikram.rao@chaiwala.co",
        "The active_customers view still uses 90 days. Need to fix that.",
    ),
]


def build_slack_export(folder: Path):
    folder.mkdir(parents=True)
    slack_ids = {person["email"]: f"U{index:04d}" for index, person in enumerate(PEOPLE)}
    users = [
        {
            "id": slack_ids[person["email"]],
            "name": person["email"].split("@")[0],
            "real_name": person["name"],
            "profile": {"email": person["email"], "real_name": person["name"]},
        }
        for person in PEOPLE
    ]
    channel_names = sorted({channel for channel, *_ in SLACK_MESSAGES})
    channels = [
        {"id": f"C{index:04d}", "name": name, "created": 1735689600}
        for index, name in enumerate(channel_names)
    ]
    write_json(folder / "users.json", users)
    write_json(folder / "channels.json", channels)

    days = {}
    for channel, day, author, text in SLACK_MESSAGES:
        posted = datetime.fromisoformat(day).replace(hour=10, tzinfo=UTC)
        messages = days.setdefault((channel, day), [])
        # Slack timestamps are unique per channel, so space messages a minute apart.
        ts = posted.timestamp() + 60 * len(messages)
        messages.append(
            {"type": "message", "user": slack_ids[author], "text": text, "ts": f"{ts:.6f}"}
        )
    for (channel, day), messages in days.items():
        (folder / channel).mkdir(exist_ok=True)
        write_json(folder / channel / f"{day}.json", messages)


def write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------- app users


def create_users_and_groups():
    """Adds the demo org, groups and users to the app. Existing ones are kept as they are."""
    with system_session() as db:
        org = db.scalar(select(Org).where(Org.name == ORG_NAME))
        if org is None:
            org = Org(name=ORG_NAME)
            db.add(org)
            db.flush()

        groups = {}
        for name in GROUPS:
            group = db.scalar(select(Group).where(Group.org_id == org.id, Group.name == name))
            if group is None:
                group = Group(org_id=org.id, name=name)
                db.add(group)
                db.flush()
            groups[name] = group

        for person in PEOPLE:
            user = db.scalar(select(User).where(User.email == person["email"]))
            if user is None:
                user = User(
                    org_id=org.id,
                    email=person["email"],
                    name=person["name"],
                    role=person.get("role", "member"),
                    password_hash=hash_password(DEMO_PASSWORD),
                )
                db.add(user)
                db.flush()
            for group_name in person["groups"]:
                db.merge(UserGroup(user_id=user.id, group_id=groups[group_name].id, org_id=org.id))
        db.commit()


def demo_sources(output_dir: Path) -> list[dict]:
    """The sources an admin would add for the demo company, with the groups that may see them."""
    every_group = list(GROUPS)
    postgres_sources = [
        {
            "type": "postgres",
            "name": database,
            "config": {
                # Same host as the admin URL, so this works in Docker and in CI.
                "dsn": make_conninfo(
                    ADMIN_DATABASE_URL,
                    user=READER_ROLE,
                    password=READER_PASSWORD,
                    dbname=database,
                ),
                "schemas": ["public"],
                "groups": [group],
            },
        }
        for database, group in [
            ("sales_db", "sales"),
            ("finance_db", "finance"),
            ("support_db", "support"),
        ]
    ]
    return [
        *postgres_sources,
        {
            "type": "files",
            "name": "drive",
            "config": {
                "path": str(output_dir / "drive"),
                "folder_groups": {"sales": ["sales"], "finance": ["finance"], "hr": ["hr"]},
            },
        },
        {
            "type": "slack_export",
            "name": "slack",
            "config": {
                "path": str(output_dir / "slack"),
                "channel_groups": {
                    "data": every_group,
                    "sales": ["sales"],
                    "finance": ["finance"],
                },
            },
        },
    ]


def register_demo_sources(output_dir: Path) -> list[int]:
    """Adds the demo sources to the app if they aren't there yet. Returns their ids."""
    with system_session() as db:
        org = db.scalar(select(Org).where(Org.name == ORG_NAME))
        source_ids = []
        for source in demo_sources(output_dir):
            existing = db.scalar(
                select(Source).where(Source.org_id == org.id, Source.name == source["name"])
            )
            if existing is None:
                existing = create_source(
                    db, org.id, source["type"], source["name"], source["config"]
                )
            source_ids.append(existing.id)
        db.commit()
        return source_ids


# ---------------------------------------------------------------- ground truth


def pii(asset: str, column: str, types: list[str], hidden: bool = False) -> dict:
    return {"asset": asset, "column": column, "pii_types": types, "hidden": hidden}


PII_COLUMNS = [
    *[
        pii(f"sales_db.public.{table}", column, [pii_type])
        for table in ["customers", "customers_new", "customers_backup_2023"]
        for column, pii_type in [
            ("full_name" if table == "customers_new" else "name", "PERSON"),
            ("email", "EMAIL"),
            ("phone", "PHONE"),
            ("city", "LOCATION"),
        ]
    ],
    pii("sales_db.public.customers_new", "address", ["LOCATION"]),
    # Cryptic column names: only the values give these away.
    pii("finance_db.public.tbl_cst_mst", "col_2", ["PERSON"], hidden=True),
    pii("finance_db.public.tbl_cst_mst", "col_3", ["PHONE"], hidden=True),
    pii("finance_db.public.tbl_cst_mst", "col_4", ["EMAIL"], hidden=True),
    pii("finance_db.public.tbl_cst_mst", "col_5", ["LOCATION"], hidden=True),
    pii("finance_db.public.tbl_cst_mst", "col_6", ["PAN"], hidden=True),
    # Free text. Only some rows have personal data in them.
    pii("support_db.public.tickets", "notes", ["PHONE", "AADHAAR"], hidden=True),
    pii("support_db.public.app_logs", "message", ["EMAIL"], hidden=True),
    *[
        pii("drive:hr/employee_list.xlsx", column, [pii_type])
        for column, pii_type in [
            ("name", "PERSON"),
            ("email", "EMAIL"),
            ("phone", "PHONE"),
            ("pan", "PAN"),
            ("aadhaar", "AADHAAR"),
            ("city", "LOCATION"),
        ]
    ],
]


DEMO_TABLES = {
    "sales_db": [
        "active_customers",
        "customers",
        "customers_backup_2023",
        "customers_new",
        "ord_dtl",
        "orders",
        "orders_v2",
        "sales_2023",
    ],
    "finance_db": ["refunds", "revenue_monthly", "tbl_cst_mst"],
    "support_db": ["app_logs", "tickets"],
}


def expected_catalogue(output_dir: Path) -> list[dict]:
    """Every asset a full sync must produce, named like the rest of this file, with its groups."""
    assets = []
    for source in demo_sources(output_dir):
        settings = source["config"]
        if source["type"] == "postgres":
            for table in DEMO_TABLES[source["name"]]:
                assets.append(
                    {"asset": f"{source['name']}.public.{table}", "groups": settings["groups"]}
                )
        elif source["type"] == "files":
            root = Path(settings["path"])
            for file in sorted(root.rglob("*.*")):
                path = file.relative_to(root).as_posix()
                folder = path.split("/")[0]
                assets.append(
                    {"asset": f"drive:{path}", "groups": settings["folder_groups"].get(folder, [])}
                )
        else:
            for channel, groups in settings["channel_groups"].items():
                assets.append({"asset": f"slack:{channel}", "groups": groups})
    return assets


def build_ground_truth(output_dir, orders, monthly_revenue, drive_facts, ticket_pii_counts) -> dict:
    march = next(row for row in monthly_revenue if row["month"] == date(2026, 3, 1))
    phone_columns = [
        f"{entry['asset']}.{entry['column']}"
        for entry in PII_COLUMNS
        if "PHONE" in entry["pii_types"] and not entry["asset"].startswith("drive:")
    ]
    return {
        "org": ORG_NAME,
        "users": [
            {"email": p["email"], "role": p.get("role", "member"), "groups": p["groups"]}
            for p in PEOPLE
        ],
        "sources": demo_sources(output_dir),
        "catalogue": expected_catalogue(output_dir),
        "duplicate_groups": [
            {"kind": "file_merge", **drive_facts["merges"][0]},
            {"kind": "file_merge", **drive_facts["merges"][1]},
            {
                "kind": "overlapping_tables",
                "assets": ["sales_db.public.orders", "sales_db.public.orders_v2"],
                "preferred": "sales_db.public.orders_v2",
            },
            {
                "kind": "overlapping_tables",
                "assets": [
                    "sales_db.public.customers",
                    "sales_db.public.customers_new",
                    "sales_db.public.customers_backup_2023",
                    "finance_db.public.tbl_cst_mst",
                ],
                "preferred": "sales_db.public.customers_new",
            },
        ],
        "pii_columns": PII_COLUMNS,
        "tickets_notes_rows_with_pii": ticket_pii_counts,
        # Not modified in the last 90 days. Postgres has no real "last modified" time for a
        # table, so for tables the signal is the newest date in the data itself.
        "stale_assets": [
            {"asset": "sales_db.public.sales_2023", "signal": "newest month is 2023-12"},
            {"asset": "sales_db.public.orders", "signal": f"newest order is {OLD_ORDERS_CUTOFF}"},
            {
                "asset": "sales_db.public.customers",
                "signal": f"newest signup is on or before {OLD_CUSTOMERS_CUTOFF}",
            },
            {"asset": "drive:finance/metrics_policy.pdf", "signal": "file modified 400 days ago"},
            {"asset": "drive:sales/orders_export_jan.csv", "signal": "file modified 280 days ago"},
            {
                "asset": "drive:sales/orders_export_jan (1).csv",
                "signal": "file modified 280 days ago",
            },
        ],
        "planted_problems": [
            "orders.amount is rupees, orders_v2.amount is paise (same column name)",
            "orders stopped updating after 2025-12-31; orders_v2 is current",
            "customers_backup_2023.signup_date is text in DD/MM/YYYY",
            "sales_2023 and sales dashboards show gross revenue; finance shows net of returns",
            "metrics_policy.pdf defines active customer as 60 days; view active_customers uses 90",
            "tbl_cst_mst has cryptic columns col_1..col_7 holding customer PII",
        ],
        "answers": {
            "latest_orders_table": "sales_db.public.orders_v2",
            "orders_v2_row_count": len(orders),
            "net_revenue_2026_03_rupees": march["net_revenue"],
            "gross_revenue_2026_03_rupees": march["gross_revenue"],
            "columns_with_phone_numbers": phone_columns,
            "customer_data_owner": "priya.sharma@chaiwala.co",
            "customer_master_table": "sales_db.public.customers_new",
            "active_customer_days_policy": 60,
            "active_customer_days_in_view": 90,
            "targets_source": "drive:finance/targets_2026.xlsx",
            "correct_sales_report": "drive:sales/sales_report_FINAL_use_this.xlsx",
            "hr_only_asset": "drive:hr/employee_list.xlsx",
        },
    }


# ---------------------------------------------------------------- main


def generate(output_dir: Path, ground_truth_path: Path) -> dict:
    random.seed(42)
    fake = Faker("en_IN")
    fake.seed_instance(42)

    customers = make_customers(fake)
    orders, lines = make_orders(customers)
    monthly_revenue = make_monthly_revenue(orders)
    tickets, ticket_pii_counts = make_tickets(customers)
    app_logs = make_app_logs(customers)

    create_reader_role()
    build_sales_db(customers, orders, lines)
    build_finance_db(customers, orders, monthly_revenue)
    build_support_db(tickets, app_logs)

    shutil.rmtree(output_dir, ignore_errors=True)
    drive_facts = build_drive(output_dir / "drive", orders, fake)
    build_slack_export(output_dir / "slack")

    create_users_and_groups()
    register_demo_sources(output_dir)

    ground_truth = build_ground_truth(
        output_dir, orders, monthly_revenue, drive_facts, ticket_pii_counts
    )
    ground_truth_path.write_text(
        "# Written by demo/generate.py. Do not edit by hand.\n"
        + yaml.safe_dump(ground_truth, sort_keys=False, allow_unicode=True, width=100)
    )
    return ground_truth


if __name__ == "__main__":
    generate(DEMO_DIR / "output", DEMO_DIR / "ground_truth.yaml")
    print(f"Demo company built. Users log in with password: {DEMO_PASSWORD}")
