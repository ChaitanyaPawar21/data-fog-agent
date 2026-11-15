# Project Context

Read this first at the start of every session. Keep it short and current.
Delete anything that is outdated or no longer useful.

## Ground rules
- Write code as simply as possible. Readable, human-like code comes first.
- `communicator/` holds the user's spec and input files. When the user names a file, read it from there and then do the work.
- `architecture/` is for diagrams and explanations of the project. Add content only when the user asks.
- `interview/problems.md` is a log of real problems and decisions worth talking about in an interview. Add entries quietly as they come up.

## Project: Data Fog Agent
- Spec: `communicator/v1.md`. It is an agentic system that finds, explains, cleans and protects scattered company data.
- Modules: Fog Eraser (catalogue + chat/SQL), PII Guard, Auto-Docs, Trust Score + Stale Alerts, File Merger, MCP Server.
- Stack (LLM is NVIDIA via OpenAI-compatible API): Python 3.12 + uv, FastAPI, Postgres 16 + pgvector, SQLAlchemy + Alembic, arq + Redis, sqlglot, Presidio, MCP SDK 2.x, Langfuse 5.x, Next.js + shadcn, Docker Compose.
- Core rule: the agent proposes and humans approve. Nothing is ever deleted, only archived.
- Build phases 0 to 9. All built as of 2026-11-15; only the real server deploy is open.

## Code location
- `data-fog-agent/` at `X:\PROJECTS\data-fog-agent`. It has its own CLAUDE.md, README.md and PROGRESS.md.
- The git repo root is `X:\PROJECTS` (remote `origin` = github.com/ChaitanyaPawar21/data-fog-agent), so CI is at
  `X:\PROJECTS\.github\workflows\ci.yml` (backend, frontend, evals jobs) with `working-directory: data-fog-agent/...`.
- Backend: `backend/app/` (config, db, models, auth, agent, mcp_server, merger, trust, worker, main, routers/). Tests in `backend/tests/`.
- Frontend: `frontend/` (Next.js 16 + shadcn). Read `frontend/AGENTS.md`: Next 16 docs are in node_modules.
- Demo: `demo/generate.py` (mounted at `/demo` in backend + worker), output in `demo/output/` (gitignored).

## Key decisions so far
- RLS: the app uses the `app` role (not the superuser), FORCE RLS, and the org is passed as a transaction-local `app.org_id` set in an `after_begin` hook. `system_session()` is the bypass for login, CLI, workers and API-token lookup.
- Auth: JWT access token kept in memory, refresh token in an httpOnly cookie on `/api/auth`. Next.js proxies `/api` and `/mcp` to the backend, so there's no CORS.
- Embeddings: `vector(1536)`, Gemini `gemini-embedding-2` asked for 1536 dims (OpenAI-compatible endpoint), always PII-masked before sending.
- Connectors return changed assets + a cursor of every present asset; sync archives the rest (never deletes).
- MCP: `/mcp` is stateless Streamable HTTP (2 API workers), behind `TokenAuth` (personal API tokens from `/settings`,
  only the SHA-256 stored, 60 requests/min per user). Tools call `agent.run_tool(actor="mcp")`, so permissions,
  SQL safety and masking are the chat agent's. `propose_merge` is MCP-only and only creates a pending proposal.
- Langfuse: on only when both keys are set; `app/llm.py` then uses Langfuse's drop-in OpenAI client. An empty
  `LANGFUSE_HOST` is dropped at startup (langfuse 5 would use "" as the address).
- A new chat conversation is saved together with its first answer (the `conversation` event is the last one in the stream).

## Current flow / status
- Phases 0-7 done by 2026-11-14, Phases 8 and 9 on 2026-11-15. See PROGRESS.md for each phase.
- Evals: `evals/run_evals.py` checks PII recall, merge detection and 51 questions (`--prepare` syncs/scans first).
- Chat answers render as markdown; a result table with a name column and a number column also gets a bar chart.
- Measured numbers: `data-fog-agent/metrics/PERF_LOG.md` (append only, real runs only; coverage via
  `uv run --with pytest-cov pytest --cov=app`, not a project dep).
- README: `data-fog-agent/README.md` (mermaid architecture diagram, `docs/demo.gif`); the repo root has a short pointer README.
- Interview architecture PDF: `architecture/data-fog-agent-architecture.pdf` and user guide `data-fog-agent/docs/how-to-use.pdf`
  both still describe Phases 0-3. Rebuild with reportlab only when the user asks.
- LLM: NVIDIA (user's choice), via the OpenAI SDK with `LLM_BASE_URL`. Chat `nvidia/nemotron-3.5-lightning-30b-a3b`
  (tool calling); Auto-Docs `DOCS_MODEL=nvidia/nemotron-3.5-bolt-e4b`. Don't load the claude-api skill for this project.
- Keys in `.env`: `LLM_API_KEY`, `DOCS_API_KEY` (NVIDIA) and `EMBEDDING_API_KEY` (Gemini) are set. Langfuse keys are empty.
- Inside containers run Python with `docker compose exec backend uv run python` (plain `python` has no venv).
- Dev admin `admin@chaiwala.co` uses the user's own password. Demo members use `chaiwala-demo`.
- `users.md` (5 demo logins) and `demo-data.md` (sample rows + the traps; regenerate with `demo/dump_data.py`) are in the project root.

## Gotchas
- Git Bash: use `MSYS_NO_PATHCONV=1` for `/demo` and `/evals` paths. Heredocs in this shell collapse `\\` to `\`,
  so write files with regexes or Windows paths through the Write tool, not a heredoc.
- New Next route folders and CSS changes need `docker compose restart frontend`. The backend migrates on startup.
- Running pytest rebuilds the demo databases (same seeded data), so don't run it while evals are running.
- The network here can be very slow to PyPI and GitHub (`uv add` took about 50 minutes on 2026-11-15; the spaCy
  model wheel would not download at all). The backend Dockerfile uses a BuildKit cache mount for uv, so failed
  builds resume and rebuilds take seconds. The cache lives in Docker's build cache, not in the image, so
  `docker builder prune` means downloading everything (including the slow spaCy model) again.
- The image sets `UV_NO_SYNC=1`: `uv run` never installs. After `uv add`, run `uv sync` in backend and worker.
- Images rebuilt 2026-11-15 (mcp, langfuse, presidio baked in). The production stack was run locally over
  https://localhost with them and torn down (volumes removed, `.env.prod` deleted).
- Browser checks: `playwright-core` + Edge (`channel: "msedge"`) from a scratchpad folder (Playwright MCP has no Chrome).
  The demo GIF is 12 such screenshots joined with Pillow.

## Next steps
- User: get a VPS + domain, then follow `data-fog-agent/docs/deploy.md`.
- User: add `LLM_API_KEY` (and optionally `EMBEDDING_API_KEY`) as GitHub Actions secrets so the eval job asks the questions too.
- Langfuse was only checked against a local recorder; set real keys to see traces in a Langfuse project.
