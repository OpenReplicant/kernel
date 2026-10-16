# Build context: the repository root.
FROM python:3.13.16-slim-bookworm
WORKDIR /app
COPY modules/belief-corroboration/requirements.txt .
# Optional build secret "ca": a CA bundle for build machines behind a TLS-intercepting proxy.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY modules/belief-corroboration/belief_corroboration /app/belief_corroboration
ENV PYTHONUNBUFFERED=1
USER nobody
CMD ["python", "-m", "belief_corroboration"]
