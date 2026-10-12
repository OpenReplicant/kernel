"""Shared chain nodes, the bridge, and the RuleGo configuration."""
import pytest
import requests

from conftest import wait_for
from kernel import claims, db
from kernel.rulego import ChainFailed


def test_vector_search_node(notes, conn, rulego):
    for key, emb in [("east", [1, 0, 0]), ("north", [0, 1, 0]), ("up", [0, 0, 1])]:
        rulego.execute("notes_set_status", {"note": key, "status": "placed", "embedding": emb})
    out = rulego.execute("notes_similar", {"label": "note", "vector": [0.9, 0.2, 0], "k": 2})
    assert [r["key"] for r in out["rows"]] == ["east", "north"]
    assert out["rows"][0]["distance"] < out["rows"][1]["distance"]
    with conn.transaction():
        conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(r[0] for r in conn.execute(
            "EXPLAIN MATCH (n:note) RETURN n.key ORDER BY n.embedding <-> '[1,0,0]'::vector LIMIT 1"))
    assert "note_embedding" in plan


def test_graph_read_node_is_read_only(notes, rulego):
    rulego.execute("notes_set_status", {"note": "delta", "status": "draft"})
    out = rulego.execute("notes_read", {"cypher": "MATCH (n:note) WHERE n.key = %(key)s RETURN n.key",
                                        "params": {"key": "delta"}})
    assert out["rows"] == [["delta"]]
    with pytest.raises(ChainFailed, match="read-only"):
        rulego.execute("notes_read", {"cypher": "CREATE (:note {key: 'sneaky'})"})


def test_graph_write_refuses_sourceless_command(notes, conn):
    from kernel.nodes.graph_write import write
    with pytest.raises(ValueError, match="needs a source"):
        write(conn, {"claims": []}, "notes", "0.1.0", causation=None, depth=0)


def test_exec_node_is_disabled(rulego):
    chain = {"ruleChain": {"id": "test_exec", "name": "test_exec", "root": True},
             "metadata": {"nodes": [{"id": "x", "type": "exec", "name": "x",
                                     "configuration": {"cmd": "ls", "args": ["/"], "replaceData": True}}],
                          "connections": []}}
    rulego.deploy(chain)
    with pytest.raises(ChainFailed, match="cmd not allowed"):
        rulego.execute("test_exec", {})
    requests.delete(f"{rulego.url}/api/v1/rules/test_exec", timeout=5)


def test_bridge_catches_up_after_downtime(notes, conn, bridge):
    """Rows written while no bridge runs are delivered when one starts (no new NOTIFY needed)."""
    with db.activation(conn):
        src = claims.ensure_source(conn, "command", {"test": "catch-up"})
        me = claims.ensure_agent(conn, "test-runner", kind="person")
        eps = claims.ensure_thing(conn, "note", "epsilon")
        claims.assert_claim(conn, eps, "notes.status", "queued", method="stated", confidence=1.0,
                            source=src, agent=me, span="$.test")
    assert pending_rows(conn) >= 4
    bridge()
    assert wait_for(lambda: pending_rows(conn) == 0, timeout=15)


def pending_rows(conn):
    n = conn.execute("SELECT count(*) FROM kernel.outbox WHERE status = 'pending'").fetchone()[0]
    conn.commit()
    return n
