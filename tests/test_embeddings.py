"""The optional embeddings path, against a fake OpenAI-compatible endpoint.

Embeddings come from the gateway, before any kernel call: they are added to created nodes
(replacing anything a model sends), used by lookup_entities' vector stage and by schema-slice
ranking. A failing or malformed endpoint falls back to names and trigrams.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import psycopg
import pytest

from gateway.embeddings import HttpEmbedder
from kernel import admin
from kernel.testing import gateway_client

pytestmark = pytest.mark.anyio

# Hand-made directions: synonyms point the same way, so only the vector stage can match them.
DIRECTIONS = {"accounts payable": [1.0, 0.0, 0.0, 0.0], "creditors ledger": [0.98, 0.1, 0.0, 0.0]}


def vector_for(text: str) -> list[float]:
    for name, vector in DIRECTIONS.items():
        if name == text.lower():
            return vector
    digest = hashlib.sha256(text.encode()).digest()
    return [0.0, *(b / 255 for b in digest[:3])]


class FakeEndpoint:
    def __init__(self, status: int = 200, drop_one: bool = False) -> None:
        self.status = status
        self.drop_one = drop_one
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"body": body, "auth": request.headers.get("authorization")})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "down"})
        texts = body["input"][:-1] if self.drop_one else body["input"]
        data = [{"index": i, "embedding": vector_for(t)} for i, t in enumerate(texts)]
        return httpx.Response(200, json={"data": data})


def embedder(endpoint: FakeEndpoint) -> HttpEmbedder:
    return HttpEmbedder(
        "http://embeddings.test/v1/embeddings", "test-embed", "key", transport=httpx.MockTransport(endpoint)
    )


async def call(client: Any, tool: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result = await client.call_tool(tool, args)
    return bool(result.is_error), json.loads(result.content[0].text)


async def create_concept(client: Any, name: str, **extra: Any) -> tuple[bool, dict[str, Any]]:
    _, head = await call(client, "query_log", {"limit": 1})
    return await call(
        client,
        "write",
        {
            "claim": {"text": f"{name} is a concept", "basis": "observed", "modality": "descriptive"},
            "read_at_offset": head["head_offset"],
            "ops": [{"op": "create", "ref": "$c", "kind": "concept", "name": name, **extra}],
        },
    )


def stored_embedding(dbname: str, node_id: str) -> str | None:
    with psycopg.connect(admin.dsn_for(admin.admin_dsn(), dbname)) as conn:
        row = conn.execute("SELECT embedding::text FROM kernel.nodes WHERE id = %s", [node_id]).fetchone()
    return row[0] if row else None


async def test_gateway_embeds_creates_and_resolves_synonyms(dbname: str) -> None:
    endpoint = FakeEndpoint()
    async with gateway_client(dbname, embedder(endpoint)) as client:
        error, written = await create_concept(client, "Accounts payable", embedding=[9, 9, 9, 9])
        assert not error
        # The gateway's vector is stored; the one the model sent is dropped.
        assert stored_embedding(dbname, written["refs"]["$c"]) == "[1,0,0,0]"
        assert endpoint.requests[0]["body"] == {"model": "test-embed", "input": ["Accounts payable"]}
        assert endpoint.requests[0]["auth"] == "Bearer key"

        _, found = await call(client, "lookup_entities", {"queries": [{"name": "Creditors ledger"}]})
        candidate = found["results"][0]["candidates"][0]
        assert (candidate["name"], candidate["stage"], candidate["band"]) == (
            "Accounts payable",
            "vector",
            "high",
        )

        error, doc = await create_concept(client, "Creditors ledger")
        assert error and doc["type"] == "urn:wmk:write:duplicate"
        assert doc["candidates"][0]["stage"] == "vector"


async def test_schema_slice_is_reranked_and_description_vectors_cached(dbname: str) -> None:
    endpoint = FakeEndpoint()
    async with gateway_client(dbname, embedder(endpoint)) as client:
        error, first = await call(
            client, "get_schema_slice", {"passage": "Who approves invoices?", "limit": 3}
        )
        assert not error and len(first["kinds"]) == 3 and len(first["edges"]) == 3
        asked = len(endpoint.requests[-1]["body"]["input"])
        assert asked > 1 + 6  # the passage plus the wider lexical slice
        await call(client, "get_schema_slice", {"passage": "Who approves invoices?", "limit": 3})
        assert endpoint.requests[-1]["body"]["input"] == ["Who approves invoices?"]


@pytest.mark.parametrize(
    "endpoint",
    [FakeEndpoint(status=500), FakeEndpoint(drop_one=True)],
    ids=["endpoint down", "wrong vector count"],
)
async def test_a_broken_endpoint_falls_back_to_names(dbname: str, endpoint: FakeEndpoint) -> None:
    async with gateway_client(dbname, embedder(endpoint)) as client:
        error, written = await create_concept(client, "Accounts payable")
        assert not error and stored_embedding(dbname, written["refs"]["$c"]) is None
        _, found = await call(client, "lookup_entities", {"queries": [{"name": "accounts  payable"}]})
        assert found["results"][0]["candidates"][0]["stage"] == "normalized"
        error, sliced = await call(client, "get_schema_slice", {"passage": "invoices", "limit": 2})
        assert not error and len(sliced["kinds"]) == 2
