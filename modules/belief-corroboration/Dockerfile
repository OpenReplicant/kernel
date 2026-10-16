# Build context: the repository root. Standard library only.
FROM python:3.13.16-slim-bookworm
WORKDIR /app
COPY modules/belief-corroboration/belief_corroboration /app/belief_corroboration
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
USER nobody
CMD ["python", "-m", "belief_corroboration"]
