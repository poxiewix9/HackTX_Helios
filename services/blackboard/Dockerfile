# ARCO blackboard service image. Build context must be the repo root:
#   docker build -f services/blackboard/Dockerfile .
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

WORKDIR /app

# Workspace manifests first for layer caching.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/shared-schemas/pyproject.toml packages/shared-schemas/pyproject.toml
COPY services/blackboard/pyproject.toml services/blackboard/pyproject.toml
RUN mkdir -p packages/shared-schemas/python/arco_schemas services/blackboard/arco_blackboard \
 && touch packages/shared-schemas/python/arco_schemas/__init__.py \
          services/blackboard/arco_blackboard/__init__.py
RUN uv sync --frozen --no-dev --package arco-blackboard

# Sources.
COPY packages/shared-schemas packages/shared-schemas
COPY services/blackboard services/blackboard
RUN uv sync --frozen --no-dev --package arco-blackboard

WORKDIR /app/services/blackboard
EXPOSE 8001

# Default: migrate. Compose overrides the command for the API container.
CMD ["python", "-m", "arco_blackboard.migrate"]
