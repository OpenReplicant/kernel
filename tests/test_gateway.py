"""The gateway over MCP (in-process): its tools, RFC 9457 problems, as-of reads and telemetry."""

from __future__ import annotations

import json
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from gateway import otel, problems
from gateway.tools import TOOL_NAMES

pytestmark = pytest.mark.anyio


async def call(client: Any, tool: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result = await client.call_tool(tool, args)
    return result.is_error, json.loads(result.content[0].text)


async def seed(client: Any) -> dict[str, Any]:
    _, src = await call(
        client,
        "ingest_source",
        {
            "content": "# Approvals\n\nDana approved invoices from 2025. Sam took over in March 2026.",
            "media_type": "text/markdown",
            "title": "HR note",
        },
    )
    chunk = src["chunks"][0]["id"]
    _, slice_ = await call(client, "get_schema_slice", {"passage": "Sam took over invoice approval"})
    _, written = await call(
        client,
        "write",
        {
            "claim": {
                "text": "Sam took over invoice approval from Dana in March 2026",
                "source": chunk,
                "quote": "Sam took over in March 2026",
                "basis": "reported",
                "modality": "descriptive",
            },
            "read_at_offset": slice_["head_offset"],
            "ops": [
                {"op": "create", "ref": "$dana", "type": "Agent", "kind": "human", "name": "Dana Ruiz"},
                {"op": "create", "ref": "$sam", "type": "Agent", "kind": "human", "name": "Sam Ortiz"},
                {
                    "op": "create",
                    "ref": "$role",
                    "kind": "role",
                    "namespace": "bpm",
                    "name": "Invoice approver",
                },
                {
                    "op": "assert",
                    "ref": "$d",
                    "edge": "implements",
                    "from": "$dana",
                    "to": "$role",
                    "valid_from": "2025-01-01",
                    "valid_to": "2026-03-01",
                },
                {
                    "op": "assert",
                    "ref": "$s",
                    "edge": "implements",
                    "from": "$sam",
                    "to": "$role",
                    "valid_from": "2026-03-01",
                },
            ],
        },
    )
    return {"chunk": chunk, **written}


async def test_exactly_the_kernel_tools(gateway: Any) -> None:
    tools = await gateway.list_tools()
    assert {t.name for t in tools.tools} == set(TOOL_NAMES)
    read_only = {t.name for t in tools.tools if t.annotations and t.annotations.read_only_hint}
    assert read_only == {"lookup_entities", "get_schema_slice", "query_graph", "query_log"}


async def test_write_read_and_cite_round_trip(gateway: Any) -> None:
    written = await seed(gateway)
    assert written["resolution"] == "resolved" and written["offset"] >= 2
    error, found = await call(
        gateway, "lookup_entities", {"queries": [{"name": "sam ortiz", "type": "Agent"}]}
    )
    assert not error and found["results"][0]["candidates"][0]["node_id"] == written["refs"]["$sam"]
    error, rows = await call(
        gateway,
        "query_graph",
        {
            "cypher": "MATCH (a:Agent)-[e:implements]->(r:Entity {name: $role}) "
            "RETURN a.name AS who, e ORDER BY who",
            "params": {"role": "Invoice approver"},
        },
    )
    assert not error and [r["who"] for r in rows["rows"]] == ["Dana Ruiz", "Sam Ortiz"]
    assert (
        rows["rows"][0]["e"]["belief_status"] == "accepted"
        and rows["rows"][0]["e"]["from"] == written["refs"]["$dana"]
    )
    _, then = await call(
        gateway,
        "query_graph",
        {"cypher": "MATCH (a:Agent)-[e:implements]->(r) RETURN a.name AS who, e", "valid_at": "2025-06-01"},
    )
    assert [r["who"] for r in then["rows"]] == ["Dana Ruiz"]
    _, before = await call(
        gateway,
        "query_graph",
        {"cypher": "MATCH (a:Agent)-[e:implements]->(r) RETURN a.name AS who, e", "known_at_offset": 1},
    )
    assert before["rows"] == []
    _, log = await call(gateway, "query_log", {"node_id": written["refs"]["$sam"]})
    assert [e["offset"] for e in log["entries"]] == [written["offset"]]
    assert log["entries"][0]["claim"]["source"] == written["chunk"]
    error, cited = await call(
        gateway, "cite", {"sentences": [{"text": "Sam approves invoices.", "edges": [written["refs"]["$s"]]}]}
    )
    assert not error and len(cited["cites"][0]["assertion_ids"]) == 1


async def test_a_batch_writes_in_order_and_carries_refs(gateway: Any) -> None:
    _, src = await call(
        gateway,
        "ingest_source",
        {"content": "Payments depends on the Ledger service.", "title": "Arch note", "collection": "arch"},
    )
    chunk = src["chunks"][0]["id"]
    _, head = await call(gateway, "query_log", {"limit": 1})
    reported = {"source": chunk, "basis": "reported", "modality": "descriptive", "run": "$run"}
    error, batch = await call(
        gateway,
        "write_batch",
        {
            "read_at_offset": head["head_offset"],
            "writes": [
                {
                    "claim": {
                        "text": "A run maps the note.",
                        "source": chunk,
                        "basis": "observed",
                        "modality": "descriptive",
                    },
                    "ops": [
                        {"op": "create", "ref": "$run", "type": "Event", "kind": "extraction", "name": "r1"}
                    ],
                },
                {
                    "claim": {
                        "text": "Payments and the Ledger service are components.",
                        "quote": "Payments depends on the Ledger service",
                        **reported,
                    },
                    "ops": [
                        {"op": "create", "ref": "$p", "kind": "component", "name": "Payments"},
                        {"op": "create", "ref": "$l", "kind": "component", "name": "Ledger service"},
                    ],
                },
                {
                    "claim": {
                        "text": "Payments depends on the Ledger service.",
                        "quote": "Payments depends on the Ledger service",
                        **reported,
                    },
                    "ops": [{"op": "assert", "edge": "depends_on", "from": "$p", "to": "$l"}],
                },
                {
                    "claim": {
                        "text": "The run is complete.",
                        "source": chunk,
                        "basis": "observed",
                        "modality": "descriptive",
                    },
                    "ops": [{"op": "close_run", "run": "$run"}],
                },
            ],
        },
    )
    assert not error, batch
    assert [w["offset"] for w in batch["written"]] == list(
        range(head["head_offset"] + 1, head["head_offset"] + 5)
    )
    run = batch["written"][0]["refs"]["$run"]
    edge = batch["written"][2]["ops"][0]
    assert (
        edge["from"] == batch["written"][1]["refs"]["$p"] and edge["to"] == batch["written"][1]["refs"]["$l"]
    )
    _, log = await call(gateway, "query_log", {"collection": "arch", "order": "asc"})
    assert [e["claim"].get("run") for e in log["entries"]] == [None, run, run, None]


async def test_a_batch_stops_at_the_first_rejection(gateway: Any) -> None:
    _, head = await call(gateway, "query_log", {"limit": 1})
    observed = {"basis": "observed", "modality": "descriptive"}
    error, doc = await call(
        gateway,
        "write_batch",
        {
            "read_at_offset": head["head_offset"],
            "writes": [
                {
                    "claim": {"text": "Billing is a component.", **observed},
                    "ops": [{"op": "create", "ref": "$b", "kind": "component", "name": "Billing"}],
                },
                {
                    "claim": {"text": "Billing is a wombat.", **observed},
                    "ops": [{"op": "create", "kind": "wombat", "name": "Billing"}],
                },
                {"claim": {"text": "Never written.", **observed}, "ops": []},
            ],
        },
    )
    assert error and doc["batch_index"] == 1 and doc["not_attempted"] == 1
    assert doc["type"] == problems.PROBLEM_TYPES["types"].uri and doc["rule"]
    assert [w["claim_id"] for w in doc["written"]] and len(doc["written"]) == 1
    _, after = await call(gateway, "query_log", {"limit": 5})
    assert after["head_offset"] == head["head_offset"] + 1


async def test_rejections_are_rfc9457_problem_documents(gateway: Any) -> None:
    written = await seed(gateway)
    error, doc = await call(
        gateway,
        "write",
        {
            "claim": {
                "text": "Dana still approves",
                "source": written["chunk"],
                "quote": "Dana approved invoices from 2025",
                "basis": "reported",
                "modality": "descriptive",
            },
            "read_at_offset": written["offset"],
            "ops": [
                {
                    "op": "assert",
                    "edge": "implements",
                    "from": written["refs"]["$dana"],
                    "to": written["refs"]["$role"],
                    "valid_from": "2026-04-01",
                }
            ],
        },
    )
    assert error
    assert doc["type"] == "urn:wmk:rule:cardinality" and doc["title"] == "Single-valued edge conflict"
    assert doc["status"] == 409 and doc["rule"] == "bpm.approver_cardinality"
    assert written["refs"]["$role"] in doc["detail"] and doc["candidates"] == []
    # A quote that is not in the cited chunk is refused, with the sentence it most resembles.
    error, doc = await call(
        gateway,
        "write",
        {
            "claim": {
                "text": "Sam took over in April 2026",
                "source": written["chunk"],
                "quote": "Sam took over in April 2026",
                "basis": "reported",
                "modality": "descriptive",
            },
            "read_at_offset": written["offset"],
            "ops": [],
        },
    )
    assert error and doc["type"] == "urn:wmk:rule:provenance" and doc["rule"] == "kernel.quote_in_source"
    assert doc["nearest"] == "Sam took over in March 2026." and doc["field"] == "claim.quote"
    error, doc = await call(
        gateway,
        "write",
        {
            "claim": {"text": "x", "basis": "observed", "modality": "descriptive"},
            "read_at_offset": written["offset"],
            "ops": [{"op": "create", "kind": "proces", "name": "Billing"}],
        },
    )
    assert error and doc["type"] == "urn:wmk:rule:types" and doc["nearest"][0] == "process"
    error, doc = await call(
        gateway,
        "write",
        {
            "claim": {"text": "x", "basis": "observed", "modality": "descriptive"},
            "read_at_offset": written["offset"],
            "ops": [{"op": "create", "type": "Agent", "kind": "human", "name": "dana ruiz"}],
        },
    )
    assert error and doc["type"] == "urn:wmk:write:duplicate"
    assert doc["candidates"][0]["node_id"] == written["refs"]["$dana"]
    error, doc = await call(gateway, "query_graph", {"cypher": "MATCH (n) SET n.name = 'x' RETURN n"})
    assert error and doc["type"] == "urn:wmk:read:not-read-only"
    error, doc = await call(gateway, "query_graph", {"cypher": "MATCH (n RETURN n"})
    assert error and doc["type"] == "urn:wmk:read:invalid-query"


def test_problem_mapping() -> None:
    doc = problems.from_kernel(
        {
            "problem": "stale",
            "rule": "kernel.stale_read",
            "detail": "changed",
            "changed": [{"node_id": "x", "offset": 3}],
            "head_offset": 3,
            "secret": "drop me",
        }
    )
    assert doc == {
        "type": "urn:wmk:write:stale-read",
        "title": "Stale read",
        "status": 409,
        "detail": "changed",
        "rule": "kernel.stale_read",
        "candidates": [],
        "changed": [{"node_id": "x", "offset": 3}],
        "head_offset": 3,
    }
    for kind, ptype in problems.PROBLEM_TYPES.items():
        assert ptype.uri.startswith("urn:wmk:") and 400 <= ptype.status < 600, kind


@pytest.fixture
def spans() -> Any:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    original = trace._TRACER_PROVIDER
    trace._TRACER_PROVIDER = provider
    otel._tracer = provider.get_tracer(otel.TRACER_NAME)
    yield exporter
    trace._TRACER_PROVIDER = original
    otel._tracer = trace.get_tracer(otel.TRACER_NAME)


async def test_no_personal_data_in_telemetry(gateway: Any, spans: InMemorySpanExporter) -> None:
    secret = "Zebulon Quixote-Marsh"
    _, src = await call(
        gateway, "ingest_source", {"content": f"{secret} lives at 9 Elm Road and approves invoices."}
    )
    _, head = await call(gateway, "query_log", {"limit": 1})
    await call(
        gateway,
        "write",
        {
            "claim": {
                "text": f"{secret} approves invoices",
                "source": src["chunks"][0]["id"],
                "quote": f"{secret} lives at 9 Elm Road and approves invoices",
                "basis": "reported",
                "modality": "descriptive",
            },
            "read_at_offset": head["head_offset"],
            "ops": [
                {
                    "op": "create",
                    "type": "Agent",
                    "kind": "human",
                    "name": secret,
                    "identity": {"email": "zq@example.test"},
                }
            ],
        },
    )
    await call(
        gateway,
        "write",
        {  # a rejection carries the name in its detail, never in telemetry
            "claim": {"text": f"{secret} again", "basis": "observed", "modality": "descriptive"},
            "read_at_offset": head["head_offset"] + 1,
            "ops": [{"op": "create", "type": "Agent", "kind": "human", "name": secret}],
        },
    )
    await call(gateway, "lookup_entities", {"queries": [{"name": secret}]})
    await call(
        gateway, "query_graph", {"cypher": "MATCH (a:Agent {name: $n}) RETURN a", "params": {"n": secret}}
    )
    await call(gateway, "cite", {"sentences": [{"text": f"{secret} approves invoices."}]})
    finished = spans.get_finished_spans()
    assert any(s.name == otel.SPAN_KERNEL_WRITE for s in finished)
    assert any(e.name == otel.EVENT_LOG_ENTRY for s in finished for e in s.events)
    assert any(e.name == otel.EVENT_REJECTION for s in finished for e in s.events)
    for s in finished:
        texts = [s.name, json.dumps(dict(s.attributes or {})), s.status.description or ""]
        texts += [e.name + json.dumps(dict(e.attributes or {})) for e in s.events]
        for text in texts:
            for needle in ("Zebulon", "Elm Road", "zq@example", "approves invoices"):
                assert needle not in text, (s.name, text)
        assert set((s.attributes or {}).keys()) - otel.ALLOWED_ATTRIBUTES <= {
            # set by the MCP SDK's own server span, which carries no payloads
            "mcp.method.name",
            "mcp.protocol.version",
            "jsonrpc.request.id",
            "gen_ai.operation.name",
            "gen_ai.tool.name",
            "error.type",
            "rpc.response.status_code",
        }


def test_safe_drops_unlisted_attributes() -> None:
    assert otel.safe(
        {otel.ATTR_CLAIM_ID: "clm_1", "claim.text": "Dana", otel.ATTR_RULE: None, otel.ATTR_OPS_COUNT: 3}
    ) == {otel.ATTR_CLAIM_ID: "clm_1", otel.ATTR_OPS_COUNT: 3}


async def test_profile_agent_registration_is_idempotent(gateway: Any) -> None:
    from gateway.identity import ensure_agents
    from gateway.profiles import AgentSpec, Profile

    tools = gateway.tools
    profile = Profile(
        name="eval",
        agent=AgentSpec("wmk-eval", "machine", "medium", {"profile": "eval"}),
        person=AgentSpec("Pat Lee", "human", "high", {"email": "pat@acme.test"}),
    )
    first = await ensure_agents(tools.kernel, profile)
    second = await ensure_agents(tools.kernel, profile)
    assert first == second and first.agent_id == tools.agents.agent_id and first.person_id


async def test_query_log_by_collection_spans_every_version_of_a_source(gateway: Any) -> None:
    """A collection groups versions of one source (a file over time); query_log can ask for all."""
    sources = []
    for version in ("Invoices need one approval.", "Invoices need two approvals."):
        _, src = await call(
            gateway,
            "ingest_source",
            {"content": version, "uri": "policies/approval.md", "collection": "policies#approval"},
        )
        sources.append(src)
        _, head = await call(gateway, "query_log", {"limit": 1})
        error, _ = await call(
            gateway,
            "write",
            {
                "claim": {
                    "text": version,
                    "source": src["chunks"][0]["id"],
                    "quote": version,
                    "basis": "reported",
                    "modality": "normative",
                },
                "read_at_offset": head["head_offset"],
                "ops": [],
                "unresolved": {"reason": "test"},
            },
        )
        assert not error
    _, both = await call(gateway, "query_log", {"collection": "policies#approval", "order": "asc"})
    assert [e["claim"]["text"] for e in both["entries"]] == [
        "Invoices need one approval.",
        "Invoices need two approvals.",
    ]
    _, first = await call(gateway, "query_log", {"source_id": sources[0]["source_id"]})
    assert len(first["entries"]) == 1
    _, none = await call(gateway, "query_log", {"collection": "policies#other"})
    assert none["entries"] == []
