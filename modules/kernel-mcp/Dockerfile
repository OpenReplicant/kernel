# Build context: the repository root.
FROM python:3.13.16-slim-bookworm
WORKDIR /app
COPY modules/kernel-mcp/requirements.txt .
# Optional build secret "ca": a CA bundle for build machines behind a TLS-intercepting proxy.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY modules/kernel-mcp/kernel_mcp /app/kernel_mcp
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
USER nobody
CMD ["python", "-m", "kernel_mcp", "--http"]
