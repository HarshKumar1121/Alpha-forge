# syntax=docker/dockerfile:1

# Stage 1: build the React app. vite.config.ts writes to ../backend/src/alpha_harness/web,
# so the backend folder has to exist beside frontend/.
FROM node:26-slim AS frontend
RUN npm install -g pnpm@12.6.0
WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/.node-version ./
# --ignore-scripts: the only script pnpm would skip is esbuild's, which pnpm-workspace.yaml
# already turns off on purpose; the build runs without it. Without this flag pnpm 12 fails
# the install in a fresh environment ("Ignored build scripts: esbuild").
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY frontend/ ./
RUN mkdir -p /app/backend/src/alpha_harness && pnpm build

# Stage 2: the Python backend, with the built UI copied into its web/ folder.
FROM python:3.14-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend/src \
    AH_DATA_DIR=/data
WORKDIR /app/backend

# Dependencies first, so code edits don't re-download them.
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./
# Forge finds config/forge relative to the repo root, so keep /app/backend and /app/config.
COPY config/ /app/config/
COPY --from=frontend /app/backend/src/alpha_harness/web/ /app/backend/src/alpha_harness/web/

# Shell form with exec so Railway's SIGTERM reaches uvicorn. Railway sets $PORT.
CMD ["sh", "-c", "exec .venv/bin/uvicorn alpha_harness.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
