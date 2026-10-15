# Stub OpenAI-compatible model server for sim and CI (lane W1-E, spec 004 task 4).
#
# Build context is ./src/tests; two named contexts supply the dependency lock and the tool
# contract, so nothing else of the repo is sent to the daemon:
#   docker build -f src/tests/sim/stub_llm/Dockerfile \
#     --build-context backend_lock=src/backend --build-context contracts=contracts src/tests
# Compose: see the `stub-llm` service block in the lane W1-E merge notes.
#
# Serves http://stub-llm:8000/v1 on the Compose network (and /health for the healthcheck).
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

# Same pinned runtime dependencies as the backend (FastAPI, uvicorn, PyYAML): reuse its lock.
COPY --from=backend_lock pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY --from=contracts tools.json ./contracts/tools.json
COPY sim ./tests/sim

ENV PYTHONPATH=/app/tests \
    STUB_LLM_TOOLS_JSON=/app/contracts/tools.json \
    STUB_LLM_PORT=8000

RUN useradd --system --uid 10002 --home-dir /app stubllm
USER stubllm

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=5s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["python", "-m", "sim.stub_llm"]
