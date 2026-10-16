# Build context: the repository root (the validator reads kernel/schemas and shapes/).
FROM python:3.13.16-slim-bookworm
WORKDIR /app
COPY modules/validator-shacl/requirements.txt .
# Optional build secret "ca": a CA bundle for build machines behind a TLS-intercepting proxy.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY kernel/schemas /kk/kernel/schemas
COPY shapes/*.shacl.ttl /kk/shapes/
COPY modules/validator-shacl/validator /app/validator
ENV KK_ROOT=/kk PYTHONUNBUFFERED=1
EXPOSE 8000
USER nobody
CMD ["python", "-m", "validator"]
