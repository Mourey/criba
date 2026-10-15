# Criba backend: FastAPI under uvicorn. Build context is ./src/backend.
FROM ghcr.io/astral-sh/uv:0.11.6 AS uv

FROM python:3.12.15-slim-trixie

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

# Dependencies first, so source edits don't invalidate the layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY criba ./criba
RUN uv sync --locked --no-dev

RUN useradd --system --uid 10001 --home-dir /app criba \
    && mkdir -p /data \
    && chown criba:criba /data
USER criba

EXPOSE 8000
CMD ["uvicorn", "criba.app:app", "--host", "0.0.0.0", "--port", "8000"]
