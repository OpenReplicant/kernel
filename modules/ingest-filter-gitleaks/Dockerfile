# Build context: the repository root. gitleaks is a static binary taken from its pinned image.
FROM zricethezav/gitleaks:v8.30.1 AS gitleaks
FROM python:3.13.16-slim-bookworm
COPY --from=gitleaks /usr/bin/gitleaks /usr/local/bin/gitleaks
WORKDIR /app
COPY modules/ingest-filter-gitleaks/ingest_filter /app/ingest_filter
ENV PYTHONUNBUFFERED=1 KK_REPO=/repo
EXPOSE 8000
USER nobody
CMD ["python", "-m", "ingest_filter"]
