# Performance log

Real measured numbers only. Append new entries, never rewrite old ones.

## 2026-11-14 — Baseline: tests and coverage (end of Phase 3)
- Change: none, baseline
- Metric: backend test count, pass rate, line coverage of `app/`
- Method: `docker compose exec backend uv run --with pytest-cov pytest --cov=app` (pytest-cov run ad hoc, not a project dependency); local Docker Desktop, Windows 11
- Before: n/a
- After: 65 passed / 65 (100%), 93% line coverage (1064 statements, 79 missed), 53s
- Result: baseline. Lowest coverage: `create_admin.py` 58%, `llm.py` 57%, `worker.py` 65%

## 2026-11-14 — Baseline: read API latency
- Change: none, baseline
- Metric: server latency of authenticated read endpoints (member user, 24 assets / 107 columns in the catalogue)
- Method: curl `%{time_total}` straight to the backend on :8000, 5 warmup + 50 runs per endpoint, sequential; local Docker Desktop, Windows 11
- Before: n/a
- After (median / p95):
  - `GET /api/auth/me`: 9.1ms / 11.5ms
  - `GET /api/catalog/assets`: 21.1ms / 25.4ms
  - `GET /api/catalog/assets?q=customer`: 15.4ms / 18.2ms
  - `GET /api/chat/conversations`: 10.8ms / 12.3ms
- Result: baseline. The catalogue is tiny, so these numbers show per-request overhead (auth + RLS setup + serialization), not query cost at scale

## 2026-11-14 — Auto-Docs on a small model (DOCS_MODEL)
- Change: Auto-Docs uses its own `DOCS_MODEL` / `DOCS_API_KEY` (nemotron-3.5-bolt-e4b) instead of the chat model (nemotron-3.5-lightning-30b-a3b, a reasoning model)
- Metric: time and completion tokens per docs draft, valid-JSON rate
- Method: the real Auto-Docs prompt (`build_prompt`, PII-masked samples) for 3 demo tables (refunds, active_customers, revenue_monthly), 1 warmup + 6 runs (each table twice) per model, NVIDIA hosted free API, called from the local backend container
- Before (lightning): median 19.5s (min 10.8s, max 25.4s), median 1,599 completion tokens, 6/6 valid drafts
- After (bolt): median 1.7s (min 0.8s, max 1.9s), median 121 completion tokens, 6/6 valid drafts
- Result: ~11x faster and ~13x fewer completion tokens per draft, no loss in valid output. Draft quality was checked by reading 3 drafts, not scored. Hosted API latency depends on NVIDIA's load

## 2026-11-14 — Model comparison: search, chat, Auto-Docs (`evals/compare_models.py`)
- Change: none yet, comparison only. Candidates: nemotron-3.5-embed-1b vs gemini-embedding-2 (search),
  super-vl-120b-a12b vs lightning-30b-a3b (chat, and as an "LLM reads the catalogue" search), bolt-e4b vs
  lightning (Auto-Docs)
- Method: NVIDIA hosted free API + Gemini API from the local backend container, demo catalogue (24 assets)
  - search: 20 hand-written queries with their correct assets; embed catalogue text as the app builds it,
    cosine rank; once with today's catalogue (2 descriptions), once with the drafted descriptions. The LLM
    variant gets the whole catalogue in the prompt and returns its top 3 paths
  - chat: the 20 eval questions (`questions.yaml`, same grader as `run_evals.py`), 2 runs per model, 4 threads
  - docs: the real Auto-Docs prompt for all 24 assets per model, 6 threads; drafts also read by hand
- Search, today's catalogue (hit@1 / hit@3 / MRR): gemini 65% / 85% / 0.78; nemotron-embed 70% / 90% / 0.81
  (same at 2048 and 1536 dims); super-vl picking 80% / 85% / 0.82 at ~10s per query
- Search, with drafted descriptions: gemini 75% / 90% / 0.84; nemotron-embed 85% / 95% / 0.91;
  super-vl picking 85% / 90% / 0.88 at ~13s per query (it also invented a path: `sales_db.public.revenue_monthly`)
- Chat: lightning 20/20 and 20/20, median 14s and 16s (max 33s); super-vl 19/20 and 20/20, median 33s and
  12s (max 117s). Super-vl's one miss: `ord_dtl_meaning`
- Docs: both 24/24 valid JSON, 100% column coverage. Lightning: median 18.2s, 1,640 tokens (1,506 reasoning),
  8/8 Slack facts, 2 minor issues on reading. Bolt: median 2.1s, 140 tokens, 7/8 facts, ~7 drafts with
  errors on reading, one inverted (`sales_report_final.xlsx` "has corrected amounts", it's the other file),
  invented details ("daily export", "marketing"), PAN expanded as "payment account number"
- Result: nemotron-3.5-embed-1b beats Gemini on search by 5–10 points hit@1 and is the best search option;
  an LLM reading the catalogue is no better, ~10s per query and doesn't scale past a prompt-sized catalogue.
  Lightning stays the chat model (as accurate as super-vl or better, steadier latency). Bolt is ~9x faster
  for docs but less accurate; the 3-draft timing in the entry above didn't score quality.
  Small samples (20 queries, 24 assets, 2 chat runs): treat 1–2 point gaps as noise

## 2026-11-14 — PII scan: batched Presidio + seeded sampling
- Change: `pii.py` runs Presidio over a column's samples as one batch (`BatchAnalyzerEngine`, spaCy batches of 50) instead of one call per value; `connectors/postgres.py` seeds `random()` per column (`setseed`) so a re-scan sees the same sample; the names/places fallback only applies to columns nothing else matched
- Metric: wall time of a full PII scan of the catalogue (`scan_asset` on every live asset), new alerts raised by re-scans
- Method: in the backend container against the dev catalogue, 24 assets / 107 columns, up to 200 samples per column; spaCy loaded first (cold load 2.9–3.8s, not counted); 1 warmup + 5 full scans, sequential; local Docker Desktop, Windows 11
- Before: median 48.51s (max 50.16s), 453ms per column; re-scans raised 2 wrong alerts (`tbl_cst_mst.col_5` and `col_6` gained PERSON)
- After: median 8.64s (max 8.86s), 81ms per column; re-scans raised 0 alerts
- Result: 5.6x faster full scan; tags are now stable across re-scans. Same 26 columns tagged with the same kinds as before (checked against the dev catalogue), 75/75 tests pass

## 2026-11-14 — Tests and coverage (end of Phase 5)
- Change: Phases 4 (PII Guard) and 5 (Auto-Docs) added
- Metric: backend test count, pass rate, line coverage of `app/`
- Method: `docker compose exec backend uv run --with pytest-cov pytest --cov=app` (ad hoc, as in the baseline); local Docker Desktop, Windows 11
- Before (end of Phase 3): 65 passed / 65, 93% (1064 statements, 79 missed)
- After: 75 passed / 75, 93% (1324 statements, 96 missed); new modules: `pii_scan.py` 100%, `routers/proposals.py` 100%, `pii.py` 99%, `docs_gen.py` 95%
- Result: coverage held at 93% while the code grew 24%. Suite time isn't compared: the baseline ran on a lighter machine load and before the Presidio tests existed

## 2026-11-15 — Full eval suite and MCP latency (end of Phase 9)
- Change: eval suite grown from 20 to 51 questions plus PII recall and merge detection; MCP server added
- Metric: eval pass rate; server latency of MCP requests
- Method: `run_evals.py` on the dev stack (NVIDIA chat, Gemini embeddings); MCP timed with curl `%{time_total}` straight to the backend on :8000, 3 warmup + 25 runs each, sequential; local Docker Desktop, Windows 11
- Before: 20/20 questions (2026-11-14)
- After: 51/51 questions (100%), PII recall 100% (26 columns), 0 of 2 merge groups missed; 97 backend tests pass in 147s
- MCP (median / p95): `tools/list` 19.4ms / 21.4ms; `tools/call run_sql` (count on a 3,000-row table) 54.5ms / 58.0ms
- Result: one eval run only, so the 100% is a single sample, not a stability measure
