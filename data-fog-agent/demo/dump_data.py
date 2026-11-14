"""Prints the demo data as markdown: every table, file and Slack channel, with sample rows."""
import json
import os
from pathlib import Path

import pandas as pd
import psycopg
from psycopg.conninfo import make_conninfo

# The same SELECT-only role the connectors read with.
READER_URL = "postgresql://datafog_reader:reader@db:5432/postgres"
SAMPLE = 5


def table_md(df: pd.DataFrame) -> str:
    def cell(v):
        text = "" if pd.isna(v) else str(v)
        text = text.replace("|", "\\|").replace("\n", " ")
        return text[:60] + "…" if len(text) > 60 else text

    lines = ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


for database in ["sales_db", "finance_db", "support_db"]:
    print(f"\n## {database} (Postgres)\n")
    with psycopg.connect(make_conninfo(READER_URL, dbname=database)) as db:
        tables = [r[0] for r in db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1")]
        for table in tables:
            count = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            cur = db.execute(f'SELECT * FROM "{table}" LIMIT {SAMPLE}')
            df = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
            print(f"### {table} — {count:,} rows\n\n{table_md(df)}\n")

print("\n## Google Drive folder (files)\n")
for path in sorted(Path("/demo/output/drive").rglob("*")):
    if path.is_dir():
        continue
    name = path.relative_to("/demo/output/drive")
    if path.suffix == ".pdf":
        print(f"### {name} — PDF, {path.stat().st_size:,} bytes\n")
        continue
    df = pd.read_csv(path) if path.suffix == ".csv" else pd.read_excel(path)
    print(f"### {name} — {len(df):,} rows\n\n{table_md(df.head(SAMPLE))}\n")

print("\n## Slack export\n")
people = {u["id"]: u["real_name"] for u in json.loads(Path("/demo/output/slack/users.json").read_text())}
for channel in sorted(p for p in Path("/demo/output/slack").iterdir() if p.is_dir()):
    messages = [m for day in sorted(channel.glob("*.json")) for m in json.loads(day.read_text())]
    print(f"### #{channel.name} — {len(messages)} messages\n")
    for m in messages[:SAMPLE]:
        print(f"- **{people.get(m['user'], m['user'])}**: {m.get('text', '')[:200]}")
    print()
