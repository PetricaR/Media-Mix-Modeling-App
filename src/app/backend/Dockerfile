# syntax=docker/dockerfile:1
# Multi-target Dockerfile for MMM Studio backend (`api` slim and `worker` with Meridian)

FROM python:3.12-slim AS base
# The venv is on PATH, so containers start its programs directly: `uv run` at start-up would sync the
# default `dev` group into the image's environment on every cold start.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/mmm_studio_mpl \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./

# --- Target 1: Slim FastAPI server (no TensorFlow/Meridian) ---
FROM base AS api
RUN uv sync --frozen --no-dev --no-install-project
COPY mmm_studio ./mmm_studio
RUN uv sync --frozen --no-dev
EXPOSE 8080
CMD ["uvicorn", "mmm_studio.main:app", "--host", "0.0.0.0", "--port", "8080"]

# --- Target 2: Heavy Worker (includes google-meridian + TensorFlow Probability) ---
FROM base AS worker
RUN uv sync --frozen --no-dev --extra worker --no-install-project
COPY mmm_studio ./mmm_studio
RUN uv sync --frozen --no-dev --extra worker
ENTRYPOINT ["mmm-studio"]
CMD ["worker", "--queue", "all"]
