# Build context: the repository root (the runner reads kernel/schemas; modules are mounted at /modules).
FROM python:3.13.16-slim-bookworm
WORKDIR /app
COPY modules/chain-runner-python/requirements.txt .
# Optional build secret "ca": a CA bundle for build machines behind a TLS-intercepting proxy.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY kernel/schemas /kk/kernel/schemas
COPY modules/chain-runner-python/chain_runner /app/chain_runner
ENV KK_ROOT=/kk KK_MODULES=/modules PYTHONUNBUFFERED=1
EXPOSE 8000
USER nobody
CMD ["python", "-m", "chain_runner"]
