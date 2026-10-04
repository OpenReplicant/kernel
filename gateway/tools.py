"""The seven gateway tools. Read tools never change data; write tools only call the three
kernel write functions. Every rejection reaches the model as an RFC 9457 problem document.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import psycopg
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, Field

from gateway import otel, problems
from gateway.cypher import CypherError, check_read_only, elements, parse_agtype, return_columns
from gateway.db import Kernel
from gateway.embeddings import Embedder, NoEmbedder, cosine
from gateway.identity import Agents
from gateway.problems import KernelRejection

log = logging.getLogger(__name__)

TOOL_NAMES = (
    "write",
    "lookup_entities",
    "get_schema_slice",
    "query_graph",
    "query_log",
    "ingest_source",
    "cite",
)


class Claim(BaseModel):
    text: str = Field(description="The statement, in one sentence, as the source makes it.")
    source: str | None = Field(
        None,
        description="Chunk id the claim was read from (from ingest_source). Required for basis reported.",
    )
    basis: Literal["observed", "reported", "inferred"] = Field(
        description="observed: seen directly; reported: someone said or wrote it; inferred: derived."
    )
    modality: Literal["descriptive", "predictive", "normative", "proposed", "hypothetical"] = Field(
        description="descriptive: is/was; predictive: will be; normative: should/must; "
        "proposed; hypothetical."
    )
    polarity: Literal["positive", "negative"] = Field(
        "positive", description="negative for denials; ops default to the claim's polarity."
    )
    confidence: Literal["low", "medium", "high"] | None = Field(
        None, description="How strongly the source commits to the claim. Default medium."
    )


class EntityQuery(BaseModel):
    name: str = Field(description="Name or alias as written in the passage.")
    type: Literal["Entity", "Agent", "Claim", "Event"] | None = Field(
        None, description="Node type, if known."
    )
    kind: str | None = Field(None, description="Kind, if known (role, activity, human, ...).")
    identity: dict[str, str] | None = Field(
        None, description='Identity keys, e.g. {"email": "sam@acme.test"}.'
    )


class Sentence(BaseModel):
    text: str = Field(description="One sentence of the answer.")
    edges: list[str] = Field(default_factory=list, description="Edge ids the sentence relied on.")
    claims: list[str] = Field(default_factory=list, description="Claim ids the sentence relied on.")
    assertions: list[str] = Field(default_factory=list, description="Assertion ids the sentence relied on.")


def ok(result: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result, default=str))], structured_content=result
    )


def fail(doc: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(doc, default=str))], is_error=True
    )


class Tools:
    def __init__(self, kernel: Kernel, embedder: Embedder, agents: Agents, profile: str) -> None:
        self.kernel = kernel
        self.embedder = embedder
        self.agents = agents
        self.profile = profile
        self._description_vectors: dict[str, list[float]] = {}

    # write ----------------------------------------------------------------------------

    async def write(
        self,
        claim: Claim,
        read_at_offset: Annotated[
            int, Field(description="head_offset returned by your last read of the graph (any read tool).")
        ],
        ops: Annotated[
            list[dict[str, Any]],
            Field(description="Graph operations the claim justifies. Empty for an unresolved claim."),
        ],
        unresolved: Annotated[
            dict[str, Any] | None,
            Field(
                description='Only with empty ops: why the claim could not be placed, e.g. {"reason": "..."}.'
            ),
        ] = None,
    ) -> CallToolResult:
        """Submit one claim and the graph operations it justifies. The kernel validates every operation
        against the ontology rules and commits claim, log entry and graph change together, or rejects with
        a problem document naming the rule (fix and retry; after two failed retries send the claim with
        empty ops so it is kept as unresolved).

        Operations (node ids come from lookup_entities or query_graph; "$name" refs point at nodes
        created earlier in the same payload):
        - {"op": "create", "ref": "$sam", "type": "Entity"|"Agent"|"Event", "kind": ..., "name": ...,
           "namespace"?, "aliases"?, "identity"?, "props"?, "status"?, "start"?, "end"?, "distinct_from"?}
        - {"op": "assert", "edge": <edge or specialisation>, "from": id|$ref, "to": id|$ref,
           "valid_from"?, "valid_to"?, "props"?, "polarity"?, "ref"?}            a new or repeated fact
        - {"op": "assert", "edge_id": ..., "valid_from"?, "valid_to"?, "polarity"?}  support, deny or
           change the window of an existing edge (e.g. close it with valid_to)
        - {"op": "assert", "claim_id": ..., "polarity"?}                          support or deny a claim
        - {"op": "link"|"unlink", "from": id, "to": id}                           same_as between duplicates
        - {"op": "promote", "about"?: [ids], "about_edges"?: [edge ids]}          make this claim a Claim node
        - {"op": "transition", "node": id, "status": ...}                          lifecycle status
        - {"op": "redact", "node": id, "fields"?: [...]} or {"op": "redact", "claim": claim id}
        Dates are YYYY-MM-DD or ISO 8601; windows are [valid_from, valid_to). There is no delete:
        retract with polarity "negative" or a supersedes edge.

        The result's "edges" give each touched edge's state afterwards. A contested edge is an
        outcome, not a failure: another source disagrees, and both stay on record.
        """
        payload: dict[str, Any] = {
            "claim": claim.model_dump(exclude_none=True),
            "read_at_offset": read_at_offset,
            "ops": ops,
        }
        if unresolved is not None:
            payload["unresolved"] = unresolved
        with otel.span(
            otel.SPAN_KERNEL_WRITE,
            **{
                otel.ATTR_AGENT_ID: self.agents.agent_id,
                otel.ATTR_OPS_COUNT: len(ops),
                otel.ATTR_TOOL: "write",
            },
        ) as span:
            trace_id, span_id = otel.current_ids()
            if trace_id:
                payload["trace_id"], payload["span_id"] = trace_id, span_id
            payload["ops"] = await self._embed_creates(ops)
            try:
                result = await self.kernel.write(payload, self.agents.agent_id)
            except KernelRejection as rejection:
                doc = problems.from_kernel(rejection.detail)
                otel.rejected(span, problems.type_of(doc), doc.get("rule"))
                return fail(doc)
            except psycopg.Error as exc:
                return self._db_failure(span, exc)
            otel.set_attributes(
                span,
                **{
                    otel.ATTR_LOG_OFFSET: result["offset"],
                    otel.ATTR_ENTRY_ID: str(result["entry_id"]),
                    otel.ATTR_CLAIM_ID: result["claim_id"],
                    otel.ATTR_RESOLUTION: result["resolution"],
                    otel.ATTR_CONFLICTS: len(result["conflicts"]),
                },
            )
            otel.log_entry_committed(
                span, result["offset"], str(result["entry_id"]), result["claim_id"], len(result["conflicts"])
            )
            otel.resolution_bands(["ambiguous" for _ in result.get("ambiguous", [])])
            return ok(result)

    async def _embed_creates(self, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Embeddings belong to the gateway: drop any the model sent, add ours for created nodes."""
        cleaned = [
            {k: v for k, v in op.items() if k != "embedding"} if isinstance(op, dict) else op for op in ops
        ]
        creates = [
            op for op in cleaned if isinstance(op, dict) and op.get("op") == "create" and op.get("name")
        ]
        if not creates:
            return cleaned
        vectors = await self.embedder.embed([str(op["name"]) for op in creates])
        if vectors:
            for op, vector in zip(creates, vectors, strict=True):
                op["embedding"] = vector
        return cleaned

    # lookup_entities ----------------------------------------------------------------------

    async def lookup_entities(
        self,
        queries: Annotated[
            list[EntityQuery], Field(description="Names to resolve, with type and kind if known.")
        ],
        limit: Annotated[int, Field(ge=1, le=20, description="Candidates per name.")] = 5,
    ) -> CallToolResult:
        """Ranked resolution candidates for names: identity keys, then normalised exact names and aliases,
        then trigram similarity, then embeddings. Bands: certain and high mean "use this id"; ambiguous
        means you decide (reuse the id, or create a new node listing it in distinct_from). Returns
        head_offset for your next write."""
        try:
            head = await self.kernel.head_offset()
            vectors = await self.embedder.embed([q.name for q in queries]) if queries else []
            results = []
            for i, q in enumerate(queries):
                candidates = await self.kernel.resolve_candidates(
                    q.name, q.type, q.kind, q.identity, vectors[i] if vectors else None, limit
                )
                otel.resolution_bands([c["band"] for c in candidates])
                results.append({"query": q.model_dump(exclude_none=True), "candidates": candidates})
        except psycopg.Error as exc:
            return self._db_failure(None, exc)
        return ok({"head_offset": head, "results": results})

    # get_schema_slice ---------------------------------------------------------------------

    async def get_schema_slice(
        self,
        passage: Annotated[str, Field(description="The passage you are about to extract claims from.")],
        namespaces: Annotated[
            list[str] | None, Field(description="Limit entity kinds to these namespaces, e.g. ['bpm'].")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=50, description="Kinds and edges to return.")] = 12,
    ) -> CallToolResult:
        """The node types, kinds, edges (with specialisations) and rules most relevant to a passage, each
        with its label and description. Read it before extracting so your ops use allowed terms."""
        try:
            fetch = limit if isinstance(self.embedder, NoEmbedder) else limit * 3
            result = await self.kernel.schema_slice(passage, namespaces, fetch)
        except psycopg.Error as exc:
            return self._db_failure(None, exc)
        result = await self._rerank(passage, result, limit)
        return ok(result)

    async def _rerank(self, passage: str, result: dict[str, Any], limit: int) -> dict[str, Any]:
        texts = [f"{x['label']}: {x['description']}" for key in ("kinds", "edges") for x in result[key]]
        missing = [t for t in texts if t not in self._description_vectors]
        vectors = await self.embedder.embed([passage, *missing])
        if vectors:
            self._description_vectors.update(zip(missing, vectors[1:], strict=True))
            query = vectors[0]
            for key in ("kinds", "edges"):
                for x in result[key]:
                    vector = self._description_vectors[f"{x['label']}: {x['description']}"]
                    x["score"] = round(x["score"] + cosine(query, vector), 4)
                result[key].sort(key=lambda x: (-x["score"], x["name"]))
        for key in ("kinds", "edges"):
            result[key] = result[key][:limit]
        return result

    # query_graph --------------------------------------------------------------------------

    async def query_graph(
        self,
        cypher: Annotated[
            str,
            Field(
                description="Read-only openCypher over graph 'world'. Labels: Entity, Agent, Claim, Event; "
                "edge labels are kernel edges (implements, part_of, flows_to, ...). Properties: id, name, "
                "kind, namespace, status; edges carry edge, kind, from, to, valid_from, valid_to, "
                "belief_status, belief_score. Use $name parameters."
            ),
        ],
        params: Annotated[dict[str, Any] | None, Field(description="Values for $name parameters.")] = None,
        valid_at: Annotated[
            str | None, Field(description="Keep only rows whose edges were true in the world at this date.")
        ] = None,
        known_at_offset: Annotated[
            int | None,
            Field(
                description="Answer as believed at this log offset: hides later nodes and edges "
                "and recomputes belief."
            ),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 100,
    ) -> CallToolResult:
        """Run a read-only Cypher query. Results carry belief status and validity windows; valid_at and
        known_at_offset answer "what was true then" and "what did we believe then". Returns head_offset
        for your next write."""
        try:
            check_read_only(cypher)
            columns = return_columns(cypher)
            moment = _parse_moment(valid_at) if valid_at else None
        except CypherError as exc:
            return fail(problems.problem(exc.kind, exc.detail))
        except ValueError:
            return fail(problems.problem("query", "valid_at must be a date (YYYY-MM-DD) or an ISO 8601 time"))
        try:
            head = await self.kernel.head_offset()
            raw = await self.kernel.cypher(cypher, params or {}, columns, limit + 1)
        except psycopg.errors.QueryCanceled:
            return fail(problems.problem("query", "the query ran too long; narrow it or add LIMIT"))
        except (
            psycopg.errors.SyntaxError,
            psycopg.errors.DataException,
            psycopg.errors.UndefinedFunction,
        ) as exc:
            return fail(problems.problem("query", _first_line(exc)))
        except psycopg.Error as exc:
            return self._db_failure(None, exc)

        rows = [[parse_agtype(cell) for cell in row] for row in raw[:limit]]
        if known_at_offset is not None:
            found = [e for row in rows for e in elements(row)]
            state = await self.kernel.state_as_of(
                sorted({e["id"] for e in found if e["element"] == "vertex"}),
                sorted({e["id"] for e in found if e["element"] == "edge"}),
                known_at_offset,
            )
            for e in found:
                as_of = state["nodes" if e["element"] == "vertex" else "edges"].get(e["id"], {})
                for key in (
                    "status",
                    "belief_status",
                    "belief_score",
                    "valid_from",
                    "valid_to",
                    "window_agreed",
                ):
                    e.pop(key, None)
                e.update(as_of)
            rows = [row for row in rows if all(e.get("known", True) for e in elements(row))]
            for row in rows:
                for e in elements(row):
                    e.pop("known", None)
        if moment is not None:
            rows = [row for row in rows if all(_valid_at(e, moment) for e in elements(row))]
        return ok(
            {
                "head_offset": head,
                "columns": columns,
                "rows": [dict(zip(columns, row, strict=True)) for row in rows],
                "truncated": len(raw) > limit,
                "as_of": {"valid_at": valid_at, "known_at_offset": known_at_offset},
            }
        )

    # query_log ----------------------------------------------------------------------------

    async def query_log(
        self,
        source_id: str | None = None,
        collection: Annotated[
            str | None, Field(description="Entries citing any version of the sources in this collection.")
        ] = None,
        chunk_id: str | None = None,
        agent_id: str | None = None,
        node_id: Annotated[str | None, Field(description="Entries that touched this node.")] = None,
        edge_id: Annotated[str | None, Field(description="Entries that asserted on this edge.")] = None,
        since: Annotated[str | None, Field(description="Recorded at or after (ISO 8601).")] = None,
        until: Annotated[str | None, Field(description="Recorded before (ISO 8601).")] = None,
        after_offset: int | None = None,
        before_offset: int | None = None,
        resolution: Literal["resolved", "unresolved"] | None = None,
        order: Literal["asc", "desc"] = "desc",
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> CallToolResult:
        """Log entries (claim, provenance and the operations applied) by source or collection, agent, node,
        edge, record time or offset. Use resolution="unresolved" to find claims still waiting to be placed."""
        filters = {
            k: v
            for k, v in {
                "source_id": source_id,
                "collection": collection,
                "chunk_id": chunk_id,
                "agent_id": agent_id,
                "node_id": node_id,
                "edge_id": edge_id,
                "since": since,
                "until": until,
                "after_offset": after_offset,
                "before_offset": before_offset,
                "resolution": resolution,
                "order": order,
                "limit": limit,
            }.items()
            if v is not None
        }
        try:
            return ok(await self.kernel.query_log(filters))
        except (psycopg.errors.InvalidDatetimeFormat, psycopg.errors.DatetimeFieldOverflow):
            return fail(problems.problem("query", "since and until must be ISO 8601 times"))
        except psycopg.Error as exc:
            return self._db_failure(None, exc)

    # ingest_source ------------------------------------------------------------------------

    async def ingest_source(
        self,
        content: Annotated[str, Field(description="The full text of the source.")],
        media_type: Literal["text/plain", "text/markdown"] = "text/plain",
        title: str | None = None,
        uri: Annotated[str | None, Field(description="Where it came from; for a turn, its position.")] = None,
        collection: Annotated[
            str | None,
            Field(description="Group of sources that count as one for belief, e.g. one conversation's id."),
        ] = None,
        author: Annotated[str | None, Field(description="Agent id of whoever wrote or said it.")] = None,
        metadata: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """Store a source and split it into chunks with stable ids and character spans. A source whose content
        hash is already known is not stored again; its chunks are returned. Cite chunk ids as claim.source."""
        source: dict[str, Any] = {
            k: v
            for k, v in {
                "content": content,
                "media_type": media_type,
                "title": title,
                "uri": uri,
                "collection": collection,
                "author": author,
                "metadata": metadata,
            }.items()
            if v is not None
        }
        with otel.span(otel.SPAN_KERNEL_INGEST, **{otel.ATTR_AGENT_ID: self.agents.agent_id}) as span:
            trace_id, span_id = otel.current_ids()
            if trace_id:
                source["trace_id"], source["span_id"] = trace_id, span_id
            try:
                result = await self.kernel.ingest_source(source, self.agents.agent_id)
            except KernelRejection as rejection:
                doc = problems.from_kernel(rejection.detail)
                otel.rejected(span, problems.type_of(doc), doc.get("rule"))
                return fail(doc)
            except psycopg.Error as exc:
                return self._db_failure(span, exc)
            otel.set_attributes(
                span,
                **{
                    otel.ATTR_SOURCE_ID: result["source_id"],
                    otel.ATTR_SOURCE_SKIPPED: result["skipped"],
                    otel.ATTR_CHUNKS: len(result["chunks"]),
                },
            )
            return ok(result)

    # cite -------------------------------------------------------------------------------

    async def cite(
        self,
        sentences: Annotated[
            list[Sentence], Field(description="Each answer sentence with what it relied on.")
        ],
        answer_id: Annotated[
            str | None, Field(description="Your id for the answer; one is made if omitted.")
        ] = None,
    ) -> CallToolResult:
        """Record which assertions each sentence of an answer relied on (edges and claims resolve to the
        assertions that counted for them). Sentences that relied on nothing are recorded as untraced."""
        cite: dict[str, Any] = {"sentences": [s.model_dump() for s in sentences]}
        if answer_id:
            cite["answer_id"] = answer_id
        with otel.span(
            otel.SPAN_KERNEL_CITE,
            **{otel.ATTR_AGENT_ID: self.agents.agent_id, otel.ATTR_SENTENCES: len(sentences)},
        ) as span:
            trace_id, span_id = otel.current_ids()
            if trace_id:
                cite["trace_id"], cite["span_id"] = trace_id, span_id
            try:
                result = await self.kernel.cite(cite, self.agents.agent_id)
            except KernelRejection as rejection:
                doc = problems.from_kernel(rejection.detail)
                otel.rejected(span, problems.type_of(doc), doc.get("rule"))
                return fail(doc)
            except psycopg.Error as exc:
                return self._db_failure(span, exc)
            otel.set_attributes(span, **{otel.ATTR_ANSWER_ID: result["answer_id"]})
            return ok(result)

    # ------------------------------------------------------------------------------------

    def _db_failure(self, span: Any, exc: psycopg.Error) -> CallToolResult:
        # Only the error class is logged: database messages can echo payload values.
        log.error("kernel call failed: %s (%s)", type(exc).__name__, exc.sqlstate)
        if span is not None:
            otel.rejected(span, problems.PROBLEM_TYPES["internal"].uri, None)
        if isinstance(exc, psycopg.OperationalError):
            return fail(problems.problem("unavailable", "the kernel database is not reachable; retry later"))
        return fail(
            problems.problem("internal", f"the kernel failed ({type(exc).__name__}); retry or report")
        )


def _parse_moment(value: str) -> str:
    text = value.strip()
    if len(text) == 10:
        text += "T00:00:00+00:00"
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_at(element: dict[str, Any], moment: str) -> bool:
    if element.get("element") != "edge":
        return True
    start, end = element.get("valid_from"), element.get("valid_to")
    return (start is None or start <= moment) and (end is None or moment < end)


def _first_line(exc: psycopg.Error) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
