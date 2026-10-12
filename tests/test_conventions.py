"""The graph conventions the database enforces, whatever writes (docs/CORE.md)."""
import pytest
from psycopg.types.json import Jsonb

from kernel import claims, db


@pytest.fixture
def ctx(notes, conn):
    """A source, an agent and a note to write claims with."""
    with db.activation(conn):
        src = claims.ensure_source(conn, "document", {"text": "the sky is blue"}, "test:doc")
        me = claims.ensure_agent(conn, "tester", kind="person")
        sky = claims.ensure_thing(conn, "note", "sky")
    return {"source": src, "agent": me, "sky": sky}


def refused(conn, cypher, *params, match=None):
    with pytest.raises(Exception, match=match):
        with conn.transaction():
            conn.execute(cypher, params or None)


def test_claim_needs_provenance(conn, ctx):
    base = {"predicate": "notes.status", "value": "x", "method": "stated", "confidence": 0.5,
            "source": ctx["source"], "agent": ctx["agent"], "span": "$.text"}
    for missing, why in [("method", "needs method"), ("confidence", "numeric confidence"),
                         ("source", "needs a source"), ("agent", "needs a source and an agent"),
                         ("span", "needs a span")]:
        props = {k: v for k, v in base.items() if k != missing}
        refused(conn, "CREATE (:claim %s)", Jsonb(props), match=why)
    refused(conn, "CREATE (:claim %s)", Jsonb({**base, "method": "guessed"}), match="needs method")
    refused(conn, "CREATE (:claim %s)", Jsonb({**base, "confidence": 1.5}), match="outside 0..1")
    refused(conn, "CREATE (:claim %s)", Jsonb({**base, "status": "superseded"}), match="born held")
    # inferred needs no span
    with conn.transaction():
        conn.execute("CREATE (:claim %s)", (Jsonb({**base, "method": "inferred", "span": None}),))


def test_times_and_causation_are_stamped_by_the_database(conn, ctx):
    with db.activation(conn, causation="outbox:42", depth=2):
        cid = claims.assert_claim(conn, ctx["sky"], "notes.status", "blue", method="observed", confidence=0.8,
                                  source=ctx["source"], agent=ctx["agent"], span="$.text")
        conn.execute("MATCH (c:claim) WHERE id(c) = %s::graphid RETURN 1", (cid,))
    c = claims.why(conn, cid)["claim"]
    assert c["causation"] == "outbox:42" and c["depth"] == 2
    with conn.transaction():
        forged = conn.execute("CREATE (c:claim %s) RETURN c.recorded_at", (Jsonb({
            "predicate": "notes.status", "value": "x", "method": "inferred", "confidence": 0.1,
            "source": ctx["source"], "agent": ctx["agent"], "recorded_at": "1999-01-01T00:00:00.000000Z",
            "depth": 0}),)).fetchone()[0]
    assert forged > "2026"


def test_no_edits_no_deletes(conn, ctx):
    with db.activation(conn):
        cid = claims.assert_claim(conn, ctx["sky"], "notes.status", "grey", method="inferred", confidence=0.3,
                                  source=ctx["source"], agent=ctx["agent"])
    refused(conn, "MATCH (c:claim) WHERE id(c) = %s::graphid SET c.value = 'green'", cid, match="immutable")
    refused(conn, "MATCH (c:claim) WHERE id(c) = %s::graphid REMOVE c.span", cid, match="immutable")
    refused(conn, "MATCH (c:claim) WHERE id(c) = %s::graphid DETACH DELETE c", cid, match="never deleted")
    refused(conn, "MATCH (c:claim)-[r:about]->() WHERE id(c) = %s::graphid DELETE r", cid, match="never deleted")
    refused(conn, "MATCH (s:source) WHERE id(s) = %s::graphid SET s.content = 'other'", ctx["source"], match="immutable")
    refused(conn, "MATCH (c:claim) WHERE id(c) = %s::graphid SET c.status = 'forgotten'", cid, match="immutable")
    with conn.transaction():
        claims.retract(conn, cid)
    assert claims.why(conn, cid)["claim"]["status"] == "retracted"
    refused(conn, "MATCH (c:claim) WHERE id(c) = %s::graphid SET c.status = 'superseded'", cid, match="retired")
    assert all(h["id"] != cid for h in claims.held(conn, ctx["sky"]))


def test_thing_key_is_fixed(conn, ctx):
    refused(conn, "MATCH (n:note) WHERE id(n) = %s::graphid SET n.key = 'moon'", ctx["sky"], match="key")
    with conn.transaction():  # other properties of a thing (derived data) may change
        conn.execute("MATCH (n:note) WHERE id(n) = %s::graphid SET n.embedding = [0, 0, -1]", (ctx["sky"],))


def test_only_declared_labels(conn, notes):
    refused(conn, "CREATE (:typo {key: 'x'})", match="undeclared label")
    refused(conn, "CREATE (:note {key: 'a'})-[:typo_edge]->(:note {key: 'b'})", match="undeclared label")
    refused(conn, "CREATE ({key: 'anonymous'})", match="unlabelled")


def test_fact_edges_carry_provenance(conn, ctx):
    with db.activation(conn):
        moon = claims.ensure_thing(conn, "note", "moon")
        eid = claims.relate(conn, "links_to", ctx["sky"], moon, method="inferred", confidence=0.4,
                            source=ctx["source"], agent=ctx["agent"])
    props = conn.execute("MATCH ()-[r:links_to]->() WHERE id(r) = %s::graphid RETURN properties(r)", (eid,)).fetchone()[0]
    assert props["method"] == "inferred" and props["recorded_at"] > "2026"
    refused(conn, "MATCH (a:note), (b:note) WHERE a.key = 'sky' AND b.key = 'moon' CREATE (a)-[:links_to]->(b)",
            match="needs method")
    refused(conn, "MATCH ()-[r:links_to]->() WHERE id(r) = %s::graphid SET r.confidence = 0.9", eid, match="immutable")


def test_supersede_refuses_a_retired_claim(conn, ctx):
    with db.activation(conn):
        a = claims.assert_claim(conn, ctx["sky"], "notes.status", "a", method="inferred", confidence=0.2,
                                source=ctx["source"], agent=ctx["agent"])
        claims.supersede(conn, a, value="b", method="inferred", confidence=0.3, source=ctx["source"], agent=ctx["agent"])
    with pytest.raises(ValueError, match="already retired"):
        with conn.transaction():
            claims.supersede(conn, a, value="c", method="inferred", confidence=0.3, source=ctx["source"],
                             agent=ctx["agent"])


def test_valid_time(conn, ctx):
    with db.activation(conn):
        room = claims.ensure_thing(conn, "note", "room")
        claims.assert_claim(conn, room, "notes.status", "lit", method="observed", confidence=1.0,
                            source=ctx["source"], agent=ctx["agent"], span="$.text",
                            valid_from="2026-01-01T00:00:00.000000Z", valid_to="2026-02-01T00:00:00.000000Z")
    assert [h["value"] for h in claims.held(conn, room, valid_at="2026-01-15T00:00:00.000000Z")] == ["lit"]
    assert claims.held(conn, room, valid_at="2026-03-01T00:00:00.000000Z") == []
