# The MCP gateway, and the pack installer (python -m kernel.packs). Build context: the repository root.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.8.17

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# The workspace's members (packs and the adapter kit) must be present to resolve the lock;
# only the kernel's own dependencies are installed (no dev group, no pack packages).
COPY pyproject.toml uv.lock README.md ./
COPY packs ./packs
COPY adapter-kit ./adapter-kit
RUN uv sync --frozen --no-default-groups --no-install-project

COPY kernel ./kernel
COPY gateway ./gateway
COPY evals ./evals
COPY profiles ./profiles
RUN uv sync --frozen --no-default-groups

EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["wmk-gateway"]
