# The kk image: the kernel's command and its default modules (kk init, kk serve, kk scan, ...).
# Build context: the repository root. Behind a TLS-intercepting proxy: --secret id=ca,src=<ca bundle>.
# Base: python 3.13.16 on Alpine 3.24, pinned by digest. gitleaks (the ingest filter) is a static binary
# taken from its pinned image.
FROM zricethezav/gitleaks:v8.30.1 AS gitleaks
FROM python:3.13.16-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a
COPY --from=gitleaks /usr/bin/gitleaks /usr/local/bin/gitleaks
WORKDIR /kk
COPY modules/kernel-mcp/requirements.txt req-mcp.txt
COPY modules/validator-shacl/requirements.txt req-validator.txt
COPY modules/self-encoder/requirements.txt req-encoder.txt
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r req-mcp.txt -r req-validator.txt -r req-encoder.txt
COPY pyproject.toml ./
COPY kk kk
COPY kernel kernel
COPY shapes/*.shacl.ttl shapes/
COPY modules modules
COPY examples examples
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir --no-deps . && rm -rf build *.egg-info
# Artifact bytes from kk scan (mount a volume here; owned by nobody so a fresh named volume is writable).
RUN mkdir /artifacts && chown nobody /artifacts
ENV KK_ROOT=/kk PYTHONUNBUFFERED=1 KK_ARTIFACTS=/artifacts
USER nobody
EXPOSE 8000
CMD ["kk", "serve", "--host", "0.0.0.0"]
