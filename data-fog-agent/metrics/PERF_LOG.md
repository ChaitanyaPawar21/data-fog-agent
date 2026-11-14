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
