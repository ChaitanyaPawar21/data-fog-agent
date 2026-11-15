FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
# Keep the virtualenv outside /app so mounting the source folder doesn't hide it.
# The longer timeout keeps big downloads (spaCy, pandas) from failing on a slow connection.
ENV UV_PROJECT_ENVIRONMENT=/venv UV_LINK_MODE=copy UV_HTTP_TIMEOUT=300

COPY pyproject.toml uv.lock ./
# The cache mount keeps downloaded packages between builds, so a build that fails halfway
# (a network timeout) picks up where it stopped instead of downloading everything again.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen

# Everything is installed above, so `uv run` uses the environment as it is. Without this it
# re-checks the lockfile on every start and downloads the spaCy model again to do so.
# After `uv add` in a running container, run `uv sync` to install the new package.
ENV UV_NO_SYNC=1

COPY . .
