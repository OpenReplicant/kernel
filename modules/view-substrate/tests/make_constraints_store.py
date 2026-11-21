"""Constraints and what breaks them, in a Substrate store, for the equivalence tests (D-108). Adds to whatever the
other fixtures made: one constraint of each kind Substrate's `store.violation` checks, and statements that keep or
break them (a subject with three URLs, one of them off the pattern; a string off a pattern; a verdict not among
the allowed ones; parts that are, or are not, instances of a class directly or through subclass of; no class at all).
Needs Substrate's `store` package (PYTHONPATH=<substrate checkout>).

  PYTHONPATH=/path/to/substrate python make_constraints_store.py "dbname=substrate_fixture"
"""
import sys

import psycopg
from store import Store, person  # Substrate's own

conn = psycopg.connect(sys.argv[1], autocommit=True)
s = Store(conn, actor="fixture-constraints")
pete = person("pete")
q = lambda key: s.id(key)  # noqa: E731
with s.edit("fixture: constraints"):
    x, y = s.item("Constrained thing", key="cx:x"), s.item("Another constrained thing", key="cx:y")
    cls, sub, other = (s.item("Allowed class", key="cx:class"), s.item("Allowed subclass", key="cx:subclass"),
                       s.item("Other class", key="cx:other"))
    s.say(sub, q("subclass-of"), cls, by=pete)
    whole = s.item("Constrained whole", key="cx:whole")
    pa, pb, pc = (s.item(n, key=f"cx:part-{n}") for n in ("a", "b", "c"))
    s.say(pa, q("instance-of"), sub, by=pete)        # an instance of a subclass of the class
    s.say(pb, q("instance-of"), other, by=pete)      # an instance of another class
    for p in (pa, pb, pc):                           # pc is an instance of nothing
        s.say(whole, q("has-part"), p, by=pete)
    s.constrain(q("has-part"), "value-type", pete, classes=[cls])
    s.constrain(q("described-at-url"), "single-value", pete)
    s.constrain(q("described-at-url"), "format", pete, regex="https://.*")
    s.constrain(q("metric"), "format", pete, regex="p[0-9]+")
    s.constrain(q("verdict"), "one-of", pete, values=[q("verdict-better"), q("verdict-worse")])
    for u in ("https://x.example/1", "https://x.example/2", "ftp://x.example/3"):
        s.say(x, q("described-at-url"), u, by=pete)
    s.say(y, q("described-at-url"), "https://y.example/1", by=pete)
    s.say(y, q("metric"), "p99", by=pete)
    s.say(y, q("metric"), "latency", by=pete)
    s.say(x, q("verdict"), q("verdict-better"), by=pete)
    s.say(y, q("verdict"), q("verdict-same"), by=pete)
print(conn.execute("select count(*) from store.violation").fetchone()[0], "violations in the store")
