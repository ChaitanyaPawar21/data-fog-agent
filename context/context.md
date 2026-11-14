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
- Stack (LLM is NVIDIA via OpenAI-compatible API): Python 3.12 + uv, FastAPI, Postgres 16 + pgvector, SQLAlchemy + Alembic, arq + Redis, sqlglot, Presidio, FastMCP, Next.js + shadcn, Docker Compose.
- Core rule: the agent proposes and humans approve. Nothing is ever deleted, only archived.
- Build phases 0 to 9, in order. Each phase must pass its checks before the next one starts.

## Code location
- `data-fog-agent/` at `X:\PROJECTS\data-fog-agent`. It has its own CLAUDE.md and PROGRESS.md.
- Backend: `backend/app/` (config, db, models, auth, create_admin, worker, main). Tests in `backend/tests/`.
- Frontend: `frontend/` (Next.js 16 + shadcn). Read `frontend/AGENTS.md`: Next 16 docs are in node_modules.
- Demo: `demo/generate.py` (mounted at `/demo` in backend + worker), output in `demo/output/` (gitignored).

## Key decisions so far
- RLS: the app uses the `app` role (not the superuser), FORCE RLS, and the org is passed as a transaction-local `app.org_id` set in an `after_begin` hook. `system_session()` is the bypass for login, CLI and workers.
- Auth: JWT access token kept in memory, refresh token in an httpOnly cookie on `/api/auth`. Next.js proxies `/api` to the backend, so there's no CORS.
- Embeddings: `vector(1536)`, Gemini `gemini-embedding-2` asked for 1536 dims (OpenAI-compatible endpoint), always PII-masked before sending. Kept Gemini (2026-11-14). Switching to NVIDIA `nemotron-3-embed-1b` would need 2048 dims + `input_type` query/passage.
- Connectors return changed assets + a cursor of every present asset; sync archives the rest (never deletes).

## Current flow / status
- Phases 0-3 done. Phase 3 closed 2026-11-14: real eval run 20/20 with NVIDIA chat + Gemini embeddings.
- Phase 4 (PII Guard) built 2026-11-14: 26/26 PII columns found, no false positives. See PROGRESS.md.
- Phase 5 (Auto-Docs) built 2026-11-14: drafts → `/approvals` → approve. 75 tests pass. See PROGRESS.md.
- Chat answers render as markdown (`frontend/components/markdown.tsx`, react-markdown + remark-gfm, tables use shadcn Table).
- 75 tests pass after Phase 5; 93% line coverage measured earlier at 65 tests (2026-11-14). Measured numbers: `data-fog-agent/metrics/PERF_LOG.md`
  (append only, real runs only; coverage via `uv run --with pytest-cov pytest --cov=app`, not a project dep).
- The first real browser run happened 2026-11-14 (Edge via playwright-core in the
  scratchpad, since Playwright MCP has no Chrome). It found and fixed a font bug in globals.css.
- Interview architecture PDF (user asked, 2026-11-14): `architecture/data-fog-agent-architecture.pdf`, 9 pages,
  diagrams + flows + security layers + decisions + Q&A. Rebuild it with reportlab when phases 4+ land.
- User guide: `data-fog-agent/docs/how-to-use.pdf` (8 pages). The generator script was in the session
  scratchpad, so it's gone. Rebuild with reportlab when the guide needs updating (Phases 4+).
- LLM: NVIDIA (user's choice, 2026-11-14), via the OpenAI SDK with `LLM_BASE_URL`. Model
  `nvidia/nemotron-3.5-lightning-30b-a3b` (llama-3.3-70b returns 410, retired). Don't load the claude-api skill for this project any more.
- Keys (2026-11-14): `LLM_API_KEY` (NVIDIA, chat), `DOCS_API_KEY` (NVIDIA, Auto-Docs) and `EMBEDDING_API_KEY` (Gemini) are set and verified.
- Models: chat `nemotron-3.5-lightning-30b-a3b` (tool calling); Auto-Docs `DOCS_MODEL=nvidia/nemotron-3.5-bolt-e4b`
  (19.5s → 1.7s per draft, see PERF_LOG). On this key nemotron-3-super, kimi-k2.6 = 404; deepseek-v4.1-flash, glm-5.3-flash, gpt-oss-20b timed out.
- Inside containers run Python with `docker compose exec backend uv run python` (plain `python` has no venv).
- Dev admin `admin@chaiwala.co` password is lost (the random one was in an old scratchpad). Resetting it
  was blocked by the permission classifier, so the user has to do it. Demo members use `chaiwala-demo`.
- Docker fix (2026-11-12): the vhdx owner problem; `D:\Docker\AppData-Local\wsl\disk\docker_data.vhdx.bak` can be deleted.
- Git Bash: use `MSYS_NO_PATHCONV=1` for `/demo` paths. New Next route folders and CSS changes need
  `docker compose restart frontend`. The backend migrates on startup, so don't run alembic by hand then.

- Production setup done 2026-11-14 (see PROGRESS.md "Production setup"): `docker-compose.prod.yml` +
  Caddy HTTPS + `.env.prod` + `docs/deploy.md`. Tested end to end locally over https://localhost, then
  torn down (volumes removed). Not deployed: needs the user's VPS + domain.

- `users.md` (5 demo logins, one per role/group; admin password is the user's own, not chaiwala-demo) and `demo-data.md` (sample rows of every demo table/file/channel + the traps; regenerate with `demo/dump_data.py`) are in the project root.

## Next steps
- User: get a VPS + domain, then follow `data-fog-agent/docs/deploy.md`.
- Phase 6: Trust Score + Alerts (alerts UI will show the PII alerts). Rebuild the backend image once so Presidio is baked in.
