# Build context: the repository root. Standard library only.
FROM python:3.13.16-slim-bookworm
WORKDIR /app
COPY modules/graph-decoder/graph_decoder /app/graph_decoder
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
USER nobody
CMD ["python", "-m", "graph_decoder"]
