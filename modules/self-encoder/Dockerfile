# Build context: the repository root. Runs one scan of the repository mounted at /repo, then exits:
#   docker run --rm -e DATABASE_URL=... -v "$PWD":/repo:ro -v kk-artifacts:/artifacts <image>
# The ingest filter (gitleaks, pinned) is bundled so the scan always filters before storing.
FROM zricethezav/gitleaks:v8.30.1 AS gitleaks
FROM python:3.13.16-slim-bookworm
COPY --from=gitleaks /usr/bin/gitleaks /usr/local/bin/gitleaks
WORKDIR /app
COPY modules/self-encoder/requirements.txt .
# Optional build secret "ca": a CA bundle for build machines behind a TLS-intercepting proxy.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY modules/self-encoder/self_encoder /app/self_encoder
COPY modules/ingest-filter-gitleaks/ingest_filter /app/ingest_filter
ENV PYTHONUNBUFFERED=1 KK_ARTIFACTS=/artifacts KK_ROLE=kkm_self_encoder
USER nobody
ENTRYPOINT ["python", "-m", "self_encoder", "scan", "--repo", "/repo"]
