FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
# Keep the virtualenv outside /app so mounting the source folder doesn't hide it.
ENV UV_PROJECT_ENVIRONMENT=/venv UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY . .
