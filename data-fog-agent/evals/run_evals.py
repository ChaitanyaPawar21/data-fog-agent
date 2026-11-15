"""Checks the system against the demo company's ground truth. Three parts:

1. Personal data: every PII column in ground_truth.yaml must be tagged (recall).
2. Merge detection: every group of copied files must have a merge proposal.
3. Questions: asks every question in questions.yaml as its user and grades the answer.
   Every question is a real model call, so this part is skipped without LLM_API_KEY.

Needs the demo built (demo/generate.py) and in the catalogue. `--prepare` does the second
part: it syncs every source, scans for personal data, scores trust and looks for merges,
which is what the worker would do over time.

    docker compose exec backend uv run python /evals/run_evals.py [--prepare] [--limit 20]

Exits 1 when any part is below its threshold.
"""

import argparse
import re
import sys
from pathlib import Path

import openai
import yaml
from sqlalchemy import select

from app import agent, config, merger, trust
from app.db import SessionLocal, system_session
from app.models import Asset, AssetColumn, Proposal, Source, User
from app.pii import AADHAAR, PAN, PHONE, is_aadhaar
from app.pii_scan import scan_asset
from app.sync import sync_source

EVALS_DIR = Path(__file__).parent
# Next to this folder in the repo; mounted at /demo in Docker.
GROUND_TRUTH = EVALS_DIR.parent / "demo" / "ground_truth.yaml"


def prepare_catalogue():
    """What the worker does after a sync, run here in one go."""
    with system_session() as db:
        source_ids = db.scalars(select(Source.id)).all()
    for source_id in source_ids:
        sync_source(source_id)
    with system_session() as db:
        asset_ids = db.scalars(select(Asset.id).where(Asset.archived.is_(False))).all()
    for asset_id in asset_ids:
        scan_asset(asset_id)
    trust.refresh_all()
    merger.find_merge_candidates()


def pii_recall(ground_truth: dict) -> tuple[float, list[str]]:
    """(share of ground-truth PII columns that are tagged, the ones that were missed)."""
    with system_session() as db:
        rows = db.execute(
            select(Asset.path, AssetColumn.name)
            .join(Asset, Asset.id == AssetColumn.asset_id)
            .where(AssetColumn.pii_type.is_not(None))
        ).all()
    tagged = {(path, column) for path, column in rows}
    # Ground truth names files "drive:hr/x.xlsx"; the catalogue stores "hr/x.xlsx".
    expected = [
        (entry["asset"].removeprefix("drive:"), entry["column"])
        for entry in ground_truth["pii_columns"]
    ]
    missed = [f"{path}.{column}" for path, column in expected if (path, column) not in tagged]
    return 1 - len(missed) / len(expected), missed


def missed_merge_groups(ground_truth: dict) -> list[list[str]]:
    """Ground-truth groups of copied files that no merge proposal covers."""
    with system_session() as db:
        proposals = db.scalars(select(Proposal).where(Proposal.type == "merge")).all()
    proposed = [{Path(file["path"]).name for file in p.payload["files"]} for p in proposals]
    expected = [g["files"] for g in ground_truth["duplicate_groups"] if g["kind"] == "file_merge"]
    return [files for files in expected if set(files) not in proposed]


def normalize(text: str) -> str:
    # Models also group thousands with spaces, often U+202F: "3 000" should match "3000".
    text = re.sub(r"(?<=\d)\s(?=\d{3}\b)", "", text)
    # Same for the look-alike hyphens (U+2010, U+2011): "line‑item" should match "line-item".
    text = re.sub("[‐‑]", "-", text)
    return text.lower().replace(",", "").replace("₹", "").replace("’", "'")


def grade(case: dict, answer: str, source_paths: set[str]) -> list[str]:
    """The reasons the answer fails. Empty when it passes."""
    text = normalize(answer)
    problems = []
    for expected in case.get("expect", []):
        if not any(option.lower() in text for option in expected.split("|")):
            problems.append(f"missing {expected!r}")
    for rejected in case.get("reject", []):
        if rejected.lower() in text:
            problems.append(f"contains {rejected!r}")
    for path in case.get("forbid_sources", []):
        if path in source_paths:
            problems.append(f"used {path}")
    if case.get("no_pii"):
        aadhaar = any(is_aadhaar(m) for m in AADHAAR.findall(answer))
        if PHONE.search(answer) or PAN.search(answer) or aadhaar:
            problems.append("shows personal data")
    return problems


def ask(email: str, question: str) -> tuple[str, set[str]]:
    with system_session() as db:
        user = db.scalar(select(User).where(User.email == email))
    with SessionLocal(info={"org_id": user.org_id}) as db:
        user = db.get(User, user.id)
        text, sources = "", set()
        for event in agent.answer(db, user, [], question):
            if event["type"] == "text":
                text += event["text"]
            if event["type"] == "done":
                sources = {source["path"] for source in event["sources"]}
    return text, sources


def run_questions(suite: dict, limit: int | None) -> bool:
    cases = suite["questions"][:limit]
    passed = 0
    for case in cases:
        try:
            answer, sources = ask(case["user"], case["question"])
            problems = grade(case, answer, sources)
        except openai.APIError as error:
            # One failed model call fails that question, not the whole run.
            answer, problems = "", [f"model error: {error.message}"]
        passed += not problems
        print(f"{'PASS' if not problems else 'FAIL'}  {case['id']}  {'; '.join(problems)}")
        if problems:
            print("      " + answer.strip().replace("\n", "\n      "))
        sys.stdout.flush()

    rate = passed / len(cases)
    print(f"{passed}/{len(cases)} passed ({rate:.0%}), threshold {suite['pass_threshold']:.0%}")
    return rate >= suite["pass_threshold"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="only run the first N questions")
    parser.add_argument("--prepare", action="store_true", help="sync, scan and score first")
    args = parser.parse_args()

    suite = yaml.safe_load((EVALS_DIR / "questions.yaml").read_text(encoding="utf-8"))
    ground_truth = yaml.safe_load(GROUND_TRUTH.read_text(encoding="utf-8"))
    if args.prepare:
        prepare_catalogue()

    recall, missed_columns = pii_recall(ground_truth)
    print(f"PII recall {recall:.0%}, threshold {suite['pii_recall_threshold']:.0%}")
    for column in missed_columns:
        print(f"      not tagged: {column}")

    missed_groups = missed_merge_groups(ground_truth)
    print(f"Merge detection: {len(missed_groups)} group(s) of copied files missed")
    for files in missed_groups:
        print(f"      not proposed: {', '.join(files)}")

    print()
    if config.LLM_API_KEY:
        questions_ok = run_questions(suite, args.limit)
    else:
        questions_ok = True
        print("Questions skipped: LLM_API_KEY is not set.")

    ok = recall >= suite["pii_recall_threshold"] and not missed_groups and questions_ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
