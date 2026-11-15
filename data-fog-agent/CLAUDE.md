# Data Fog Agent

The full build spec lives outside this repo at `../communicator/v1.md`. Read it before starting a phase.
Progress per phase is in `PROGRESS.md`.

## Rules
- Readable, human-like code first. Plain functions by default, no unneeded classes or layers.
- Clear specific names, short functions, comments only for non-obvious reasoning.
- The agent proposes, humans approve. Never delete data, only archive.
- Never send unmasked PII or credentials to the LLM. Never run non-SELECT SQL on connected sources.
- Every permission check lives in backend code and is shared by UI, agent and MCP.

## Running
- `cp .env.example .env`, fill in `JWT_SECRET`, then `docker compose up --build`.
- Create the first admin: `docker compose exec backend uv run python -m app.create_admin --org "Chaiwala Co." --email admin@chaiwala.co --name Admin`
- Tests: `docker compose exec backend uv run pytest`
- Demo company: `docker compose exec backend uv run python /demo/generate.py` (rebuilds the demo
  DBs and `demo/output/`, writes `demo/ground_truth.yaml`; demo users' password is `chaiwala-demo`).
  From Git Bash, set `MSYS_NO_PATHCONV=1` first or the `/demo` path gets mangled.
- Lint: `uv run ruff check . && uv run ruff format --check .` (in `backend/`)
- New Python dependency: `docker compose exec backend uv add <name>`, then `uv sync` in the backend
  and worker containers (the image sets `UV_NO_SYNC=1`, so `uv run` never installs by itself),
  and `docker compose build backend worker` to bake it into the image.
- MCP: `/mcp`, with an API token from the Settings page. Evals: `uv run python /evals/run_evals.py`.
- After `docker compose up`, sync the demo sources from the Sources page (admin) or wait for the
  6-hourly cron. Embeddings need `EMBEDDING_API_KEY` (Gemini) in `.env`; without it they're skipped.
- New frontend route folders aren't always noticed by the dev server on Windows mounts:
  `docker compose restart frontend`. Run `npx next typegen` before `tsc` for new routes.

## Database notes
- The app connects as the `app` role, not `postgres`. Superusers skip row-level security.
- Every table except `orgs` has row-level security on `org_id`. Use `get_db` + `get_current_user`
  in API routes. Use `system_session()` only for login, CLI scripts and workers.
