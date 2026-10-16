# Build context: the repository root. Standard library only. Writes the RuleGo chain folder (volume).
FROM python:3.13.16-slim-bookworm
WORKDIR /app
COPY modules/chain-decoder-rulego/chain_decoder /app/chain_decoder
ENV PYTHONUNBUFFERED=1 KK_RULEGO_DIR=/rulego/workflows/kk/rules
EXPOSE 8000
CMD ["python", "-m", "chain_decoder"]
