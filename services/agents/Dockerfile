# ARCO agents service image. Build context must be the repo root:
#   docker build -f services/agents/Dockerfile .
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
COPY services/agents/pyproject.toml services/agents/pyproject.toml
COPY services/edge-capture/pyproject.toml services/edge-capture/pyproject.toml
COPY services/base-station/pyproject.toml services/base-station/pyproject.toml
RUN mkdir -p packages/shared-schemas/python/arco_schemas services/blackboard/arco_blackboard services/agents/arco_agents services/edge-capture/arco_edge services/base-station/arco_base \
 && touch packages/shared-schemas/python/arco_schemas/__init__.py \
          services/blackboard/arco_blackboard/__init__.py \
          services/agents/arco_agents/__init__.py \
          services/edge-capture/arco_edge/__init__.py \
          services/base-station/arco_base/__init__.py
RUN uv sync --frozen --no-dev --package arco-agents

# Sources + the small sample data the demo population points at.
COPY packages/shared-schemas packages/shared-schemas
COPY services/blackboard services/blackboard
COPY services/agents services/agents
COPY data/samples data/samples
RUN uv sync --frozen --no-dev --package arco-agents

WORKDIR /app/services/agents
EXPOSE 8003

CMD ["python", "-m", "arco_agents.api"]
