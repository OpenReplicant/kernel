"""A small Substrate store for the end-to-end run (README): a whole, its parts and roles, a candidate, a v1 relation,
an unknown property, a deprecated and a preferred statement, a quantity, a time, a span. Needs Substrate's `store`
package (PYTHONPATH=<substrate checkout>) and a database with its migrations 001, 003 and 004 applied.

  PYTHONPATH=/path/to/substrate python make_fixture_store.py "dbname=substrate_fixture"
"""
import sys

import psycopg
from store import Store, interpreter, parser, person  # Substrate's own

conn = psycopg.connect(sys.argv[1], autocommit=True)
s = Store(conn, actor="fixture")
pete, claude = person("pete"), interpreter("claude-code", "2.1.0")
with s.edit("fixture: the Workbench and its database"):
    span = s.read_span("file:docs/framework.md", "L10-12", "The workbench keeps its claims in Postgres.")[0]
    mapper = parser("map-skill", "run-1", span=span)
    role, iface = s.id("role"), s.id("interface")
    wb = s.item("Workbench", key="fx:workbench", description="the thing Substrate watches", aliases=["wb"],
                prose="A page of prose that is not encoded.")
    pg, sqlite = s.item("Postgres", key="fx:postgres"), s.item("SQLite", key="fx:sqlite", description="a candidate")
    storage, sql = s.item("storage", key="fx:storage"), s.item("SQL interface", key="fx:sql")
    api, db = s.item("v1 api", key="fx:v1-api"), s.item("v1 db", key="fx:v1-db")
    latency = s.item("latency", key="fx:latency")
    s.say(storage, s.id("instance-of"), role, by=pete)
    s.say(sql, s.id("instance-of"), iface, by=pete)
    s.say(storage, s.id("requires-interface"), sql, by=pete)
    s.say(pg, s.id("provides-interface"), sql, by=claude)
    s.say(sqlite, s.id("provides-interface"), sql, by=claude)
    s.say(wb, s.id("has-part"), pg, by=mapper, qualifiers=[(s.id("object-has-role"), storage)])
    s.say(wb, s.id("has-goal"), latency, by=pete)
    s.say(sqlite, s.id("measurement"), {"amount": 12.5}, by=mapper,
          qualifiers=[(s.id("for-goal"), latency), (s.id("point-in-time"), "2026-11-17")])
    old = s.say(wb, s.id("described-at-url"), "https://old.example/wb", by=claude)
    s.say(wb, s.id("described-at-url"), "https://example.org/wb", by=pete, rank="preferred")
    s.rank(old, "deprecated", "moved")
    s.say(api, s.property("depends on (v1)", "item", key="v1:field:depends_on"), db, by=claude)
    s.say(api, s.property("foo (v1 annotation)", "string", key="v1:annotation:foo"), "bar", by=claude)
print(conn.execute("select count(*) from store.entity where kind = 'item'").fetchone()[0], "items,",
      conn.execute("select count(*) from store.statement").fetchone()[0], "statements")
