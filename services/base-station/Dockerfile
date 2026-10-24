# ARCO vision image: base station (default CMD) or edge node (override CMD).
# Build context must be the repo root:
#   docker build -f services/base-station/Dockerfile .
# Models are NOT baked in: mount ./models at /app/models (read-only) for onnx_split.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# opencv-python-headless still needs glib
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY packages/shared-schemas/pyproject.toml packages/shared-schemas/pyproject.toml
COPY services/blackboard/pyproject.toml services/blackboard/pyproject.toml
COPY services/agents/pyproject.toml services/agents/pyproject.toml
COPY services/edge-capture/pyproject.toml services/edge-capture/pyproject.toml
COPY services/base-station/pyproject.toml services/base-station/pyproject.toml
RUN mkdir -p packages/shared-schemas/python/arco_schemas services/blackboard/arco_blackboard \
             services/agents/arco_agents services/edge-capture/arco_edge services/base-station/arco_base \
 && touch packages/shared-schemas/python/arco_schemas/__init__.py \
          services/blackboard/arco_blackboard/__init__.py \
          services/agents/arco_agents/__init__.py \
          services/edge-capture/arco_edge/__init__.py \
          services/base-station/arco_base/__init__.py
RUN uv sync --frozen --no-dev --package arco-base-station

COPY packages/shared-schemas packages/shared-schemas
COPY services/edge-capture services/edge-capture
COPY services/base-station services/base-station
COPY data/samples/vision data/samples/vision
RUN uv sync --frozen --no-dev --package arco-base-station

WORKDIR /app/services/base-station
EXPOSE 8002

CMD ["python", "-m", "arco_base.api"]
