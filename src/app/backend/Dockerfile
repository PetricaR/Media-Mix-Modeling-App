# syntax=docker/dockerfile:1
# Multi-target Dockerfile for MMM Studio backend (`api` slim and `worker` with Meridian)

FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/mmm_studio_mpl \
    UV_CACHE_DIR=/tmp/uv-cache

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
CMD ["uv", "run", "uvicorn", "mmm_studio.main:app", "--host", "0.0.0.0", "--port", "8080"]

# --- Target 2: Heavy Worker (includes google-meridian + TensorFlow Probability) ---
FROM base AS worker
RUN uv sync --frozen --no-dev --extra meridian --no-install-project
COPY mmm_studio ./mmm_studio
RUN uv sync --frozen --no-dev --extra meridian
ENTRYPOINT ["uv", "run", "mmm-studio"]
CMD ["worker", "--queue", "all"]
