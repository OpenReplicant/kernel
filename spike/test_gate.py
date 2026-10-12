"""S0 gate. Needs the database and RuleGo-Server from spike/run.sh up; starts its own bridge.

Gate: a Cypher write fires a trigger -> outbox row -> RuleGo chain runs -> the chain writes
back to the graph; a vector + graph query returns the expected vertex; no outbox row is
lost while the consumer (bridge, or RuleGo itself) is down.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

SPIKE = Path(__file__).resolve().parent
DB = os.environ["DATABASE_URL"]
RULEGO_BIN = os.environ.get("RULEGO_BIN", "/tmp/claude-0/rulego-server")
RULEGO_CONF = os.environ.get("RULEGO_CONF", "/tmp/claude-0/s0/config.conf")


def q(sql, *params):
    with psycopg.connect(DB, autocommit=True) as c:
        c.execute("SET graph_path = s0")
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else None


def add_doc(name, emb):
    q("CREATE (:doc {name: %s, embedding: %s})", Jsonb(name), Jsonb(emb))


def topic_of(name):
    return q("MATCH (d:doc)-[r:about]->(t:topic) WHERE d.name = %s RETURN t.name, r.method, r.source",
             Jsonb(name))


def pending():
    return q("SELECT count(*) FROM public.outbox WHERE done_at IS NULL")[0][0]


def wait_for(pred, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


def rulego_up():
    import requests
    try:
        return requests.get("http://127.0.0.1:9090/api/v1/rules", timeout=1).ok
    except requests.RequestException:
        return False


@pytest.fixture(autouse=True)
def services_up():
    assert rulego_up(), "RuleGo-Server is not running: start it with spike/run.sh"


@pytest.fixture
def bridge():
    procs = []

    def start():
        p = subprocess.Popen([sys.executable, str(SPIKE / "bridge.py")], env={**os.environ, "POLL": "1"},
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        return p

    yield start
    for p in procs:
        if p.poll() is None:
            p.send_signal(signal.SIGINT)
            p.wait(5)


def test_vector_and_graph_query():
    """Nearest doc to a query vector, restricted by a Cypher pattern, via the HNSW index."""
    rows = q("""MATCH (d:doc)-[:about]->(t:topic {name: 'vertical'})
                RETURN d.name ORDER BY d.embedding <-> '[0.1,0.1,1]'::vector LIMIT 1""")
    assert rows == [("up",)]
    with psycopg.connect(DB, autocommit=True) as c:
        c.execute("SET graph_path = s0")
        c.execute("SET enable_seqscan = off")
        plan = "\n".join(r[0] for r in c.execute(
            "EXPLAIN MATCH (d:doc) RETURN d.name ORDER BY d.embedding <-> '[1,0,0]'::vector LIMIT 1"))
    assert "doc_emb" in plan


def test_write_fires_chain_that_writes_back(bridge):
    bridge()
    add_doc("gate-ne", [0.7, 0.7, 0.1])
    assert wait_for(lambda: topic_of("gate-ne")), "chain did not write back"
    [(topic, method, source)] = topic_of("gate-ne")
    assert (topic, method) == ("horizontal", "inferred")
    seq = int(source.split(":")[1])
    # the outbox row that started it is done, and the edge the chain wrote was itself captured
    # with the causation and depth the step set
    assert q("SELECT label, op, done_at IS NOT NULL FROM public.outbox WHERE seq = %s", seq) == [("doc", "INSERT", True)]
    assert wait_for(lambda: q("SELECT 1 FROM public.outbox WHERE label = 'about' AND causation = %s "
                              "AND depth = 1 AND done_at IS NOT NULL", source))


def test_no_loss_while_bridge_down(bridge):
    assert pending() == 0
    names = [f"gate-down-{i}" for i in range(5)]
    for i, n in enumerate(names):
        add_doc(n, [0.1 * i, 0.0, 1.0])  # all nearest to 'up' -> vertical
    assert pending() == 5 and not any(topic_of(n) for n in names)
    bridge()  # consumer comes back: catches up without a fresh NOTIFY
    assert wait_for(lambda: all(topic_of(n) for n in names) and pending() == 0)
    assert {topic_of(n)[0][0] for n in names} == {"vertical"}


def test_no_loss_while_rulego_down(bridge):
    bridge()
    subprocess.run(["pkill", "-x", "rulego-server"], check=False)
    assert wait_for(lambda: not rulego_up(), 5)
    add_doc("gate-rg-down", [1.0, 0.1, 0.0])
    assert wait_for(lambda: q("SELECT attempts FROM public.outbox WHERE done_at IS NULL")[0][0] >= 1, 10)
    assert not topic_of("gate-rg-down")
    subprocess.Popen([RULEGO_BIN, f"-c={RULEGO_CONF}"], start_new_session=True,
                     stdout=open(Path(RULEGO_CONF).parent / "rulego.log", "a"), stderr=subprocess.STDOUT)
    assert wait_for(rulego_up, 10)
    # chains persist in data_dir, so the restarted server still has s0_on_change
    assert wait_for(lambda: topic_of("gate-rg-down") and pending() == 0, 30)
