"""More of a Substrate store for the equivalence test, on top of encoder-substrate's fixture (make_fixture_store.py):
a map three levels deep (a whole, a part that is a whole, a part of that), a cycle, a role that needs two
interfaces, candidates that provide one of them or both, a part held by two wholes, two normal values beside a
preferred one, a deprecated value, a time with a clock, aliases, an external id, and items with similar names for
search. Needs Substrate's `store` package (PYTHONPATH=<substrate checkout>).

  PYTHONPATH=/path/to/substrate python make_deeper_store.py "dbname=substrate_fixture"
"""
import sys

import psycopg
from store import Store, interpreter, parser, person  # Substrate's own

conn = psycopg.connect(sys.argv[1], autocommit=True)
s = Store(conn, actor="fixture-deeper")
pete, claude = person("pete"), interpreter("claude-code", "2.1.0")
q = lambda key: s.id(key)  # noqa: E731
with s.edit("fixture: a deeper map"):
    span = s.read_span("file:docs/plan.md", "L1-2", "Postgres holds the pgvector extension and the queue.")[0]
    mapper = parser("map-skill", "run-2", span=span)
    role, iface = q("role"), q("interface")
    wb = s.item("Workbench", key="fx:workbench", description="the thing Substrate watches", aliases=["wb"],
                prose="A page of prose that is not encoded.")
    pg = s.item("Postgres", key="fx:postgres")
    sqlite = s.item("SQLite", key="fx:sqlite", description="a candidate")
    storage = s.item("storage", key="fx:storage")
    pgv, queue = s.item("pgvector", key="fx:pgvector", description="vector search in Postgres", aliases=["pg_vector", "vector extension"]), s.item("pgmq", key="fx:pgmq")
    gw = s.item("Gateway", key="fx:gateway", description="the model gateway")
    faiss, qdrant, hnsw = (s.item("FAISS", key="fx:faiss", description="a library, a candidate"),
                           s.item("Qdrant", key="fx:qdrant", description="a vector database, a candidate"),
                           s.item("hnswlib", key="fx:hnswlib"))
    postgrest = s.item("PostgREST", key="fx:postgrest", description="REST over Postgres", aliases=["pgrst"])
    vindex, mq = s.item("vector index", key="fx:vector-index"), s.item("message queue", key="fx:mq")
    knn, ann, qi = (s.item("nearest-neighbour search", key="fx:knn"), s.item("approximate index", key="fx:ann"),
                    s.item("queue interface", key="fx:queue-if"))
    s.say(vindex, q("instance-of"), role, by=pete)
    s.say(mq, q("instance-of"), role, by=pete)
    for i in (knn, ann, qi):
        s.say(i, q("instance-of"), iface, by=pete)
    s.say(vindex, q("requires-interface"), knn, by=pete)          # a role that needs two interfaces
    s.say(vindex, q("requires-interface"), ann, by=pete)
    s.say(mq, q("requires-interface"), qi, by=pete)
    s.say(pgv, q("provides-interface"), knn, by=claude)
    s.say(pgv, q("provides-interface"), ann, by=claude)
    s.say(queue, q("provides-interface"), qi, by=claude)
    s.say(faiss, q("provides-interface"), knn, by=claude)         # one of the two: not a candidate
    s.say(qdrant, q("provides-interface"), knn, by=claude)        # both: a candidate
    s.say(qdrant, q("provides-interface"), ann, by=claude)
    s.say(hnsw, q("provides-interface"), ann, by=claude)
    s.say(hnsw, q("provides-interface"), knn, by=claude)          # both, and also a part elsewhere (below)
    s.say(pg, q("has-part"), pgv, by=mapper, qualifiers=[(q("object-has-role"), vindex)])     # depth 2 from Workbench
    s.say(pg, q("has-part"), queue, by=mapper, qualifiers=[(q("object-has-role"), mq)])
    s.say(wb, q("has-part"), pg, by=mapper, qualifiers=[(q("object-has-role"), storage)])
    s.say(wb, q("has-part"), postgrest, by=claude)                 # a part with no role
    s.say(gw, q("has-part"), pg, by=mapper, qualifiers=[(q("object-has-role"), storage)])     # held by two wholes
    s.say(gw, q("has-part"), hnsw, by=mapper, qualifiers=[(q("object-has-role"), vindex)])    # a candidate that fills a role here
    s.say(queue, q("has-part"), pg, by=claude)                     # a cycle: Postgres, pgmq, Postgres
    # values, ranks, qualifiers
    s.say(pgv, q("described-at-url"), "https://example.org/pgvector", by=pete)
    s.say(pgv, q("described-at-url"), "https://example.org/pgvector-docs", by=pete)           # two normal values
    p1 = s.say(pgv, q("described-at-url"), "https://old.example/pgv", by=claude)
    s.rank(p1, "deprecated", "moved")
    s.say(pgv, q("described-at-url"), "https://example.org/pgvector-readme", by=pete, rank="preferred")  # preferred hides the normals
    s.say(qdrant, q("catalog-id"), "docker:qdrant/qdrant", by=claude)
    s.say(qdrant, q("measurement"), {"amount": 3}, by=mapper,
          qualifiers=[(q("for-goal"), s.item("latency", key="fx:latency")),
                      (q("point-in-time"), {"time": "+2026-11-17T10:30:00Z", "precision": 14})])
    s.say(faiss, q("measurement"), {"amount": 7.25}, by=[mapper, claude],
          qualifiers=[(q("for-goal"), s.item("latency", key="fx:latency")), (q("point-in-time"), "2026-11")])
print(conn.execute("select count(*) from store.entity where kind = 'item'").fetchone()[0], "items,",
      conn.execute("select count(*) from store.statement").fetchone()[0], "statements")
