"""Optional embedding endpoint (OpenAI-compatible /embeddings).

Embeddings are computed in the gateway before any kernel call, never inside the
database or a transaction. Without an endpoint, or when it fails, resolution and
schema slicing fall back to names and trigram matching.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from gateway import otel

log = logging.getLogger(__name__)


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]] | None: ...


class NoEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None


class HttpEmbedder:
    def __init__(self, url: str, model: str, api_key: str | None = None, timeout: float = 10.0) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)
        self._url = url
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        with otel.span(otel.SPAN_EMBEDDINGS, **{otel.ATTR_EMBED_COUNT: len(texts)}):
            try:
                response = await self._client.post(self._url, json={"model": self._model, "input": texts})
                response.raise_for_status()
                data = sorted(response.json()["data"], key=lambda d: d["index"])
                return [list(map(float, d["embedding"])) for d in data]
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                # The texts are never logged; only the failure type.
                log.warning("embedding endpoint failed (%s); falling back to names", type(exc).__name__)
                return None

    async def aclose(self) -> None:
        await self._client.aclose()


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0
