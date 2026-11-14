# Progress

## Phase 0 — Skeleton
- Docker Compose with Postgres 16 + pgvector, Redis, backend, worker and frontend.
- FastAPI app, env config, SQLAlchemy session, Alembic migration with every table from the spec.
- Row-level security on every table except `orgs`, keyed on `org_id`, forced for the table owner.
  The app uses a non-superuser role so the policies actually apply.
- Auth: argon2 passwords, 15 min JWT access token, 7 day refresh token in an httpOnly cookie,
  `create_admin` CLI script.
- Next.js frontend with shadcn/ui: login page and a logged-in home page. `/api` is proxied to
  the backend, so the refresh cookie works without CORS.
- GitHub Actions: ruff + pytest against a real Postgres.
- Embedding columns are `vector(1536)` (OpenAI text-embedding-3-small). Revisit in Phase 2.

Verified 2026-11-12: `docker compose up --build` works, the migration applies, 12 tests pass,
login → refresh → `/api/auth/me` works through the Next.js `/api` proxy, and the refresh
cookie is httpOnly on `/api/auth`.

## Phase 1 — Demo data
- `demo/generate.py` builds Chaiwala Co. from scratch with a fixed seed:
  - `sales_db`, `finance_db`, `support_db` with every table from the spec and all the planted fog
    (rupees vs paise in the same `amount` column, text dates, cryptic `col_1..col_7`, PII hidden
    in `tickets.notes` and `app_logs.message`, and an `active_customers` view that uses 90 days
    where the policy says 60).
  - `datafog_reader`, a SELECT-only role that is also read-only by default, for the connectors.
  - Drive folder (`sales/`, `finance/`, `hr/`) with the three sales reports, the duplicate
    January export, the metrics policy PDF, targets and the HR employee list. File mtimes are
    set so "newest file wins" and the staleness checks are deterministic.
  - Slack export (`#data`, `#sales`, `#finance`) in the standard export format.
  - The app org, groups (`sales`, `finance`, `hr`, `support`) and 8 users. Safe to re-run.
- `demo/ground_truth.yaml`: duplicate groups with expected merge counts, every PII column,
  stale assets, source configs with the expected group per source, and eval answers.
- DB tables have no real "last modified" time we can backdate, so stale tables show it in their
  data (newest date in the table). The Phase 2 postgres connector should fall back to that.
- `tests/test_demo.py` runs the generator and checks DBs, files, Slack, users and re-runs.

## Phase 2 — Connectors + catalogue
- Connectors (`app/connectors/`): `postgres`, `files`, `slack_export`. Each returns only the
  assets that changed, plus a cursor naming every asset that exists now.
  - postgres: tables and views from `information_schema`, row estimates from `pg_class`. A table
    counts as changed when its columns or Postgres's insert/update/delete counters change.
    Last modified = newest date in the table, falling back to vacuum/analyze times.
  - files: csv/xlsx headers become columns. Unchanged size + mtime is skipped without reading
    the file; a touched file with the same hash is not a change. `_archive/` is ignored.
  - slack_export: each channel is an asset, its messages become `knowledge_snippets`.
- `app/sync.py`: upserts assets, columns (in place, so descriptions and PII tags survive) and
  group permissions; archives assets that disappeared (never deletes); writes `sync_runs`.
- Source settings are Fernet-encrypted (`app/source_config.py`) and never returned by the API.
- Embeddings: OpenAI `text-embedding-3-small` via `app/llm.py`. One job fills every missing
  embedding; sync clears the embedding of a changed asset. Skipped when `EMBEDDING_API_KEY`
  is empty. Text is PII-masked first (`app/pii.py` regex rules, pulled forward from Phase 4).
- `app/permissions.py` `visible_asset_ids(user)`: admins see all, members see their groups'.
- Worker: `sync_source_job`, `embed_missing_job`, cron sync of every source every 6 hours.
- API: `GET /api/catalog/assets` (text search + source/type/PII filters), `GET
  /api/catalog/assets/{id}`, admin-only `GET/POST /api/sources`, `POST /api/sources/{id}/sync`.
- UI: catalogue list, asset detail, sources page (status, errors, sync now, add source).
- Migration 0002: unique asset path per source, unique column per asset, snippet -> channel.
- Demo now registers its 5 sources and writes the full expected catalogue to ground truth.
- Verified: a full sync matches `ground_truth.yaml` assets and groups exactly; a re-sync with
  nothing changed processes 0 assets; one edited file re-processes only that file.
- Not done yet: trust filter (scores arrive in Phase 6); vector search is used by the agent
  in Phase 3; `sample_values` arrives with PII scanning in Phase 4.

## Phase 3 — Agent + chat
- `app/agent.py`: LLM tool loop (max 8 steps) with `search_catalog`, `get_schema`, `run_sql`,
  `search_knowledge`, `find_owner`. Every tool checks `visible_asset_ids` and masks PII, and the
  question itself is PII-masked before the model sees it. The last step forces a text answer.
- `app/sql_guard.py`: single SELECT only (sqlglot), visible tables only, read-only user, READ ONLY
  transaction, 10s timeout, LIMIT 200, PII columns masked, every query and rejection audited.
- Chat API (`/api/chat`, server-sent events) and stored conversations (migration 0003).
- Chat UI (`/chat`, now the home page): streaming answer, tool status line, result table, source
  chips that link to the catalogue, conversation list.
- Evals: `evals/questions.yaml` (first 20 questions) and `evals/run_evals.py`.
- Fixed: the app was rendering in Times because `--font-sans` pointed at itself in `globals.css`.
- User guide: `docs/how-to-use.pdf` (screenshots from a real browser run as a demo member).
- Verified 2026-11-14: 61 tests pass (incl. all SQL safety tests), ruff clean, tsc + eslint clean,
  chat page and APIs work through the Next.js proxy in a real browser.
- Verified 2026-11-14 with real keys: eval run **20/20 (100%)** on `nemotron-3.5-lightning-30b-a3b`.
  Fixes on the way there: the grader now treats spaces between digit groups (U+202F) and
  look-alike hyphens (U+2011) as plain text. On the last step the model now gets a "no tool calls
  left" note (sent, never stored), because without it the model sometimes wrote a tool call as plain text.
- Charts in chat are not built yet; query results show as a table.
- 2026-11-14: switched chat from Anthropic to NVIDIA (user's choice). It goes through the OpenAI SDK
  against any OpenAI-compatible API: `LLM_BASE_URL` (default `https://integrate.api.nvidia.com/v1`),
  `LLM_MODEL` (default `nvidia/nemotron-3.5-lightning-30b-a3b`, must support tool calling). History is stored in
  OpenAI chat format. Open models have no strict tool schemas, so `parse_arguments` checks tool names
  and argument types in code and sends bad calls back to the model as errors. On the last step no
  tools are sent, which forces an answer. `anthropic` removed from dependencies. 62 tests pass.
- 2026-11-14: embeddings moved to Gemini (`gemini-embedding-2`, user's choice) through its
  OpenAI-compatible endpoint (`EMBEDDING_BASE_URL`), asking for 1536 dimensions so the existing
  `vector(1536)` columns stay. Nothing had been embedded yet, so nothing needed re-embedding.

## Production setup (pulled forward from Phase 9, 2026-11-14, user's request)
- `docker-compose.prod.yml` (project `datafog-prod`): Caddy with automatic HTTPS is the only thing
  published (80/443); Postgres and Redis stay internal. Secrets are required (`${VAR:?}`), restart
  `unless-stopped`, logs capped at 10 MB x 5, backend healthcheck on `/api/health`.
- Backend: 2 uvicorn workers, `--proxy-headers` so rate limits see the real client IP; migrations
  run once before the workers start.
- Frontend: multi-stage Dockerfile (`dev` / `build` / `prod`), Next.js `output: "standalone"`,
  runs as the `node` user. Dev compose uses `target: dev`.
- Caddy sends `/api/*` straight to FastAPI (streaming chat included) and adds HSTS and other headers.
- `infra/postgres-init-prod.sh` creates the `app` role with the password from the environment.
- Rate limits in Redis (`app/rate_limit.py`): login 10/min per IP, chat 30/min per user (spec).
- `.env.prod.example`, `docs/deploy.md` (VPS, DNS, firewall, backups, updates, troubleshooting).
- CI got a Redis service for the rate-limit tests. 65 tests pass.
- Still open from Phase 9: Langfuse tracing, evals in CI, README with diagram, an actual server.

## Phase 4 — PII Guard
- `app/pii.py` `column_pii_types`: regex rules (Aadhaar with Verhoeff, PAN, phone, email) plus
  Presidio with spaCy `en_core_web_sm` for PERSON and LOCATION, on text columns only. A column is
  tagged when 20% of non-empty samples match. Free-text columns (`notes`, `message`, `comments`...)
  are tagged on a single match and use the regex rules only, because NER finds "names" in any prose.
- The small model mixes up Indian names and cities, so when PERSON + LOCATION together pass 20%
  but neither alone does, the bigger one is tagged. Either tag masks the column.
- Connectors got `sample_values(config, path, column_names, limit=200)`: random non-null values as
  text. Never stored. Slack channels have none and are skipped.
- `app/pii_scan.py` `scan_asset`: stores the kinds in `columns.pii_type` (comma-separated, widened to
  100 chars in migration 0004). Tags only grow. New PII → one `pii_found` alert per asset, naming
  columns and kinds, never values. The worker runs `pii_scan_job` for every changed asset after a sync.
- Masking in `run_sql` and the catalogue already worked off `pii_type` (Phase 3), so tagging is what
  switches it on.
- Verified 2026-11-14: all 26 ground-truth PII columns tagged (incl. `tickets.notes` and the cryptic
  `tbl_cst_mst` columns), no other column tagged. The LLM-input test now scans first and checks real
  names, cities and addresses never reach the model. 69 tests pass.
- Known: two kind mix-ups from the small model (HR `city` → PERSON, `address` → PERSON+LOCATION).
  `en_core_web_lg` would be more accurate but is a 400 MB download.
- Presidio + spaCy are in `pyproject.toml`/`uv.lock`. The image needs `docker compose build` once
  (slow network: the downloads time out at uv's default 30s; `UV_HTTP_TIMEOUT=600` helps).

## Phase 5 — Auto-Docs
- `app/docs_gen.py` `generate_docs(asset_id)`: for undocumented assets with no pending or rejected
  draft. The LLM gets name, path, column names/types, up to 5 sample values per column and up to 8
  related Slack messages, and returns JSON (description + one line per column). Saved as a pending
  `doc` proposal; the asset goes to `description_status = proposed`. Nothing reaches the catalogue
  until a person approves.
- Personal-data columns send no values at all, only their kind ("personal data (PERSON), values
  withheld"). Masked values like "XXXXKant" told the model nothing and it described them as "masked
  examples". Untagged columns still go through the regex mask.
- Slack notes are only used if every group that can see the asset can read that channel, because the
  approved description is shown to all the asset's viewers. The worker session skips RLS, so the
  snippet query filters on org by hand.
- The NVIDIA model reasons before answering and that counts against `max_tokens` (~2,500 tokens per
  draft), so drafts get 16,000 and a cut-off reply raises instead of saving half a JSON.
- Worker: after every sync, `docs_job` for each undocumented asset (so catalogues synced before
  Auto-Docs get drafts too). `docs_job` runs the PII scan first: jobs run in parallel, and a draft must
  never sample a column before it's tagged.
- API (`routers/proposals.py`): `GET /api/proposals?status=pending`, `POST /{id}/approve`,
  `POST /{id}/reject`. Admins review everything, owners their own assets (`permissions.can_review`);
  anything else is a 404. Approving copies the text in (columns someone already approved are kept),
  clears the embedding so search picks the description up, and is audited. Rejecting resets the asset
  to `none`; a rejected draft isn't retried automatically.
- UI: `/approvals` (draft, column table, approve/reject) in the nav; the asset page links to it while
  a draft is waiting.
- Verified 2026-11-14: 75 tests pass (incl. no PII in draft prompts, Slack scoping, owner vs non-owner).
