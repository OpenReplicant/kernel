# The MCP gateway. Build context: the repository root.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.8.17

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY kernel ./kernel
COPY gateway ./gateway
COPY evals ./evals
COPY packs ./packs
COPY profiles ./profiles
RUN uv sync --frozen --no-dev

EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["wmk-gateway"]
