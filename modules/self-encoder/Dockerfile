# Build context: the repository root. The repository to scan is mounted at /repo.
FROM python:3.13.16-slim-bookworm
WORKDIR /app
COPY modules/self-encoder/requirements.txt .
# Optional build secret "ca": a CA bundle for build machines behind a TLS-intercepting proxy.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY modules/self-encoder/self_encoder /app/self_encoder
ENV PYTHONUNBUFFERED=1 KK_REPO=/repo KK_ARTIFACTS=/artifacts
EXPOSE 8000
CMD ["python", "-m", "self_encoder"]
