"""Compares candidate models on the app's real tasks. Writes nothing to the database.

    docker compose exec backend uv run python /evals/compare_models.py search
    docker compose exec backend uv run python /evals/compare_models.py chat [--runs 2]
    docker compose exec backend uv run python /evals/compare_models.py docs

search: catalogue retrieval with each embedding model (hit@1, hit@3, MRR).
chat:   the eval questions with each chat model (pass rate, seconds per answer).
docs:   Auto-Docs drafts for every asset with each model (valid JSON, column coverage,
        known facts from the Slack notes, seconds, tokens). Prints drafts as JSON lines too.
Every call is a real API call, so a run costs money and takes a while.
"""

import argparse
import json
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml
from openai import OpenAI
from run_evals import ask, grade
from sqlalchemy import select

from app import config, docs_gen, llm
from app.db import system_session
from app.embeddings import asset_embedding_text
from app.models import Asset, AssetColumn, Proposal, Source

NVIDIA = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)

# ---------------------------------------------------------------- search

# What people type, and the assets that answer it.
SEARCH_QUERIES = [
    ("latest orders data", {"sales_db.public.orders_v2"}),
    ("which customer table is current", {"sales_db.public.customers_new"}),
    ("monthly net revenue", {"finance_db.public.revenue_monthly"}),
    ("gross revenue and returns by month", {"finance_db.public.revenue_monthly"}),
    ("sales targets for 2026", {"finance/targets_2026.xlsx"}),
    ("order line items with product and quantity", {"sales_db.public.ord_dtl"}),
    ("customer support tickets", {"support_db.public.tickets"}),
    ("refunds paid back to customers", {"finance_db.public.refunds"}),
    ("employee directory", {"hr/employee_list.xlsx"}),
    ("how metrics like active customer are defined", {"finance/metrics_policy.pdf"}),
    ("customers who ordered recently", {"sales_db.public.active_customers"}),
    ("application error logs", {"support_db.public.app_logs"}),
    ("old customer backup", {"sales_db.public.customers_backup_2023"}),
    ("2023 sales by channel", {"sales_db.public.sales_2023"}),
    ("finance customer master", {"finance_db.public.tbl_cst_mst"}),
    ("corrected sales report", {"sales/sales_report_FINAL_use_this.xlsx"}),
    ("january orders export", {"sales/orders_export_jan.csv", "sales/orders_export_jan (1).csv"}),
    ("finance team discussion", {"finance"}),
    ("customer home addresses", {"sales_db.public.customers_new"}),
    (
        "customer phone numbers",
        {
            "sales_db.public.customers",
            "sales_db.public.customers_new",
            "sales_db.public.customers_backup_2023",
            "finance_db.public.tbl_cst_mst",
        },
    ),
]


def gemini(texts: list[str], kind: str) -> list[list[float]]:
    return llm.embed_texts(texts)  # what the app uses today; no query/passage distinction


def nemotron(dimensions: int | None):
    def embed(texts: list[str], kind: str) -> list[list[float]]:
        extra = {"dimensions": dimensions} if dimensions else {}
        response = NVIDIA.embeddings.create(
            model="nvidia/nemotron-3.5-embed-1b",
            input=texts,
            extra_body={"input_type": kind},
            **extra,
        )
        return [item.embedding for item in response.data]

    return embed


def catalogue_texts(with_drafts: bool) -> dict[str, str]:
    """{path: embedding text}, as the app builds it. Optionally with the drafted descriptions."""
    with system_session() as db:
        assets = db.scalars(select(Asset).where(Asset.archived.is_(False))).all()
        drafts = {
            p.payload["asset_id"]: p.payload["description"]
            for p in db.scalars(select(Proposal).where(Proposal.type == "doc"))
        }
        texts = {}
        for asset in assets:
            columns = db.scalars(select(AssetColumn.name).where(AssetColumn.asset_id == asset.id))
            description = asset.description
            if with_drafts and not description:
                description = drafts.get(asset.id)
            # A stand-in, so the draft description never touches the real row.
            stand_in = SimpleNamespace(name=asset.name, path=asset.path, description=description)
            texts[asset.path] = asset_embedding_text(stand_in, list(columns))
    return texts


def rank_scores(embed, texts: dict[str, str]) -> dict:
    paths = list(texts)
    started = time.perf_counter()
    passages = np.array(embed([texts[p] for p in paths], "passage"))
    queries = np.array(embed([q for q, _ in SEARCH_QUERIES], "query"))
    seconds = time.perf_counter() - started
    passages /= np.linalg.norm(passages, axis=1, keepdims=True)
    queries /= np.linalg.norm(queries, axis=1, keepdims=True)

    hits1 = hits3 = reciprocal = 0.0
    misses = []
    for (query, relevant), scores in zip(SEARCH_QUERIES, queries @ passages.T, strict=True):
        ranked = [paths[i] for i in np.argsort(-scores)]
        rank = next(i for i, path in enumerate(ranked, start=1) if path in relevant)
        hits1 += rank == 1
        hits3 += rank <= 3
        reciprocal += 1 / rank
        if rank > 3:
            misses.append(f"{query!r} -> rank {rank}, top was {ranked[0]}")
    n = len(SEARCH_QUERIES)
    return {
        "hit@1": hits1 / n,
        "hit@3": hits3 / n,
        "mrr": reciprocal / n,
        "seconds": seconds,
        "misses": misses,
    }


def compare_search():
    models = {
        "gemini-embedding-2 (1536)": gemini,
        "nemotron-3.5-embed-1b (2048)": nemotron(None),
        "nemotron-3.5-embed-1b (1536)": nemotron(1536),
    }
    for with_drafts in [False, True]:
        texts = catalogue_texts(with_drafts)
        label = "with drafted descriptions" if with_drafts else "catalogue as it is today"
        print(f"\n## Search: {len(SEARCH_QUERIES)} queries over {len(texts)} assets, {label}")
        print(f"{'model':32} {'hit@1':>6} {'hit@3':>6} {'MRR':>6} {'secs':>6}")
        for name, embed in models.items():
            result = rank_scores(embed, texts)
            print(
                f"{name:32} {result['hit@1']:6.0%} {result['hit@3']:6.0%} "
                f"{result['mrr']:6.2f} {result['seconds']:6.1f}"
            )
            for miss in result["misses"]:
                print(f"    miss: {miss}")


# ---------------------------------------------------------------- chat


def compare_chat(models: list[str], runs: int, threads: int):
    suite = yaml.safe_load((Path(__file__).parent / "questions.yaml").read_text())
    cases = suite["questions"]

    def run_case(case):
        started = time.perf_counter()
        try:
            answer, sources = ask(case["user"], case["question"])
            problems = grade(case, answer, sources)
        except Exception as error:  # a failed call is a failed answer, not a crashed benchmark
            problems = [f"error: {type(error).__name__}: {str(error)[:120]}"]
        return case["id"], problems, time.perf_counter() - started

    for model in models:
        # agent.py reads config.LLM_MODEL on every call, so one model at a time.
        config.LLM_MODEL = model
        for run in range(1, runs + 1):
            with ThreadPoolExecutor(threads) as pool:
                results = list(pool.map(run_case, cases))
            passed = sum(not problems for _, problems, _ in results)
            seconds = [s for _, _, s in results]
            print(
                f"\n## Chat {model} run {run}: {passed}/{len(cases)} passed, "
                f"median {statistics.median(seconds):.0f}s, max {max(seconds):.0f}s per answer"
            )
            for case_id, problems, secs in results:
                if problems:
                    print(f"    FAIL {case_id} ({secs:.0f}s): {'; '.join(problems)}")


# ---------------------------------------------------------------- docs

# Facts the Slack notes give each asset's draft. A good draft mentions them.
DOC_FACTS = {
    "sales_db.public.orders": r"orders_v2|2025",
    "sales_db.public.orders_v2": r"paise",
    "sales_db.public.ord_dtl": r"line.?item",
    "sales_db.public.customers_new": r"priya",
    "sales_db.public.customers_backup_2023": r"dd/mm|as text|text format|stored as text",
    "finance_db.public.tbl_cst_mst": r"customer master|master",
    "finance_db.public.revenue_monthly": r"\bnet\b",
    "sales/sales_report_FINAL_use_this.xlsx": r"correct",
}


def draft_prompts() -> list[tuple[str, list[str], str]]:
    """(path, column names, prompt) per live asset, built exactly as docs_gen builds them."""
    prompts = []
    with system_session() as db:
        for asset in db.scalars(select(Asset).where(Asset.archived.is_(False))):
            columns = list(
                db.scalars(
                    select(AssetColumn)
                    .where(AssetColumn.asset_id == asset.id)
                    .order_by(AssetColumn.id)
                )
            )
            source = db.get(Source, asset.source_id)
            samples = docs_gen.safe_samples(source, asset, columns)
            snippets = docs_gen.related_snippets(db, asset)
            prompt = docs_gen.build_prompt(asset, columns, samples, snippets)
            prompts.append((asset.path, [c.name for c in columns], prompt))
    return prompts


def compare_docs(models: list[str], threads: int):
    prompts = draft_prompts()

    def draft(model, path, column_names, prompt):
        started = time.perf_counter()
        try:
            response = NVIDIA.chat.completions.create(
                model=model,
                max_tokens=docs_gen.MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as error:
            return {"path": path, "error": f"{type(error).__name__}: {str(error)[:120]}"}
        choice = response.choices[0]
        parsed = docs_gen.parse_draft(choice.message.content or "", column_names)
        details = response.usage.completion_tokens_details
        return {
            "path": path,
            "seconds": time.perf_counter() - started,
            "finish": choice.finish_reason,
            "tokens": response.usage.completion_tokens,
            "reasoning_tokens": getattr(details, "reasoning_tokens", None) or 0,
            "draft": parsed,
            "coverage": (
                len(parsed["columns"]) / len(column_names) if parsed and column_names else 1
            ),
        }

    for model in models:
        with ThreadPoolExecutor(threads) as pool:
            results = list(pool.map(lambda p, model=model: draft(model, *p), prompts))
        ok = [r for r in results if r.get("draft")]
        facts_found = []
        for result in ok:
            pattern = DOC_FACTS.get(result["path"])
            if pattern:
                text = json.dumps(result["draft"]).lower()
                facts_found.append(bool(re.search(pattern, text)))
        print(f"\n## Docs {model}")
        print(f"  valid drafts:     {len(ok)}/{len(results)}")
        print(f"  column coverage:  {statistics.mean(r['coverage'] for r in ok):.0%}")
        print(f"  facts mentioned:  {sum(facts_found)}/{len(DOC_FACTS)}")
        print(f"  seconds:          median {statistics.median(r['seconds'] for r in ok):.1f}")
        print(
            f"  tokens:           median {statistics.median(r['tokens'] for r in ok):.0f} "
            f"(reasoning {statistics.median(r['reasoning_tokens'] for r in ok):.0f})"
        )
        for result in results:
            if not result.get("draft"):
                print(f"    FAILED {result['path']}: {result.get('error') or result.get('finish')}")
            pattern = DOC_FACTS.get(result["path"])
            if result.get("draft") and pattern:
                if not re.search(pattern, json.dumps(result["draft"]).lower()):
                    print(f"    missed fact in {result['path']} ({pattern})")
        for result in results:
            print("DRAFT " + json.dumps({"model": model, **result}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=["search", "chat", "docs"])
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()

    if args.task == "search":
        compare_search()
    elif args.task == "chat":
        chat_models = [
            "nvidia/nemotron-3.5-lightning-30b-a3b",
            "nvidia/nemotron-3.5-super-vl-120b-a12b",
        ]
        compare_chat(chat_models, args.runs, args.threads)
    else:
        doc_models = ["nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-3.5-bolt-e4b"]
        compare_docs(doc_models, args.threads)


if __name__ == "__main__":
    main()
