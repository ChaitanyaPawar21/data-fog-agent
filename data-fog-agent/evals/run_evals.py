"""Asks every question in questions.yaml as its user and grades the answers.

Needs the demo synced into the app database and LLM_API_KEY set. Every question is a real
model call, so a full run costs money.

    docker compose exec backend uv run python /evals/run_evals.py [--limit 20]

Exits 1 when the pass rate is below pass_threshold.
"""

import argparse
import re
import sys
from pathlib import Path

import yaml
from sqlalchemy import select

from app import agent
from app.db import SessionLocal, system_session
from app.models import User
from app.pii import AADHAAR, PHONE, is_aadhaar


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
        if PHONE.search(answer) or any(is_aadhaar(m) for m in AADHAAR.findall(answer)):
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="only run the first N questions")
    args = parser.parse_args()

    suite = yaml.safe_load((Path(__file__).parent / "questions.yaml").read_text())
    cases = suite["questions"][: args.limit]
    passed = 0
    for case in cases:
        answer, sources = ask(case["user"], case["question"])
        problems = grade(case, answer, sources)
        passed += not problems
        print(f"{'PASS' if not problems else 'FAIL'}  {case['id']}  {'; '.join(problems)}")
        if problems:
            print("      " + answer.strip().replace("\n", "\n      "))

    rate = passed / len(cases)
    print(f"\n{passed}/{len(cases)} passed ({rate:.0%}), threshold {suite['pass_threshold']:.0%}")
    sys.exit(0 if rate >= suite["pass_threshold"] else 1)


if __name__ == "__main__":
    main()
