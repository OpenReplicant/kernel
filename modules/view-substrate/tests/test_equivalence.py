#!/usr/bin/env python3
"""view-substrate against Substrate's own store (D-104).

Builds a Substrate store with Substrate's own migrations (001, 003, 004) and its `Store` class (encoder-substrate's
fixture, then a deeper one), a throwaway kernel (`kk init`, `kk serve --only validator`), runs encoder-substrate,
and asks the same question of both: store.usable / substrate.usable, store.search / substrate.search,
store.render / substrate.page, store.map and map_nodes / substrate.map and map_nodes, store.alternatives /
substrate.alternatives. Then it checks that row-level security reaches every answer (a claim in a scope the reader
is not cleared for is in none of them) and that nothing is security definer. Drops both databases and stops the
server at the end.

  SUBSTRATE=/path/to/substrate  python modules/view-substrate/tests/test_equivalence.py

Needs Postgres 16+ (PGHOST/PGUSER/PGDATABASE as for psql; a superuser) with pgvector and pg_trgm, this repository
installed (`pip install -e .`), and a Substrate checkout. Skips (exit 0) when one is missing.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SUBSTRATE = os.environ.get("SUBSTRATE", "")
PY = sys.executable


def skip(why: str):
    print(f"skip: {why}")
    sys.exit(0)


try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    skip("psycopg is not installed")
if not SUBSTRATE or not (Path(SUBSTRATE) / "store/sql/001_store.sql").exists():
    skip("SUBSTRATE does not name a Substrate checkout (store/sql/001_store.sql)")
sys.path.insert(0, str(ROOT / "modules/encoder-substrate"))
try:
    from encoder_substrate.encode import ITEMS, PROPERTIES, item_iri, literal
except ImportError:
    skip("encoder_substrate is not importable (run from the repository)")

ADMIN_DB = os.environ.get("PGDATABASE", "postgres")
try:
    admin = psycopg.connect(dbname=ADMIN_DB, autocommit=True)
    have = {r[0] for r in admin.execute("select name from pg_available_extensions where name in ('vector', 'pg_trgm')")}
    is_super = admin.execute("select rolsuper from pg_roles where rolname = current_user").fetchone()[0]
except psycopg.Error as e:
    skip(f"no Postgres: {str(e).strip()[:100]}")
if not is_super or have != {"vector", "pg_trgm"}:
    skip("needs a superuser and the pgvector and pg_trgm extensions")

RUN = f"{os.getpid()}"
SDB, KDB = f"vs_store_{RUN}", f"vs_kernel_{RUN}"
PW = secrets.token_hex(12)
HOST, PORT = os.environ.get("PGHOST", "localhost"), os.environ.get("PGPORT", "5432")
procs: list[subprocess.Popen] = []
fails: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = ""):
    global checks
    checks += 1
    if not ok:
        fails.append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def url(user: str, password: str, db: str) -> str:
    if HOST.startswith("/"):
        return f"postgresql://{user}:{password}@/{db}?host={HOST}&port={PORT}"
    return f"postgresql://{user}:{password}@{HOST}:{PORT}/{db}"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def cleanup():
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    for db in (SDB, KDB):
        admin.execute(f'drop database if exists "{db}" with (force)')


# --------------------------------------------------------------------------------------------- set up
def build():
    admin.execute(f'create database "{SDB}"')
    with psycopg.connect(dbname=SDB, autocommit=True) as c:
        for f in ("001_store", "003_components", "004_map"):
            with c.transaction():
                c.execute((Path(SUBSTRATE) / f"store/sql/{f}.sql").read_text())
    env = {**os.environ, "PYTHONPATH": SUBSTRATE}
    for script in ("modules/encoder-substrate/tests/make_fixture_store.py", "modules/view-substrate/tests/make_deeper_store.py"):
        subprocess.run([PY, str(ROOT / script), f"dbname={SDB}"], check=True, env=env, capture_output=True)
    admin.execute(f'create database "{KDB}"')
    kenv = {**os.environ, "KK_ADMIN_URL": f"dbname={KDB}", "KK_AUTHENTICATOR_PASSWORD": PW,
            "KK_JWT_SECRET": secrets.token_hex(24), "DATABASE_URL": url("kk_authenticator", PW, KDB)}
    subprocess.run([PY, "-m", "kk", "init"], check=True, env=kenv, cwd=ROOT, capture_output=True)
    procs.append(subprocess.Popen([PY, "-m", "kk", "serve", "--only", "validator", "--port", str(free_port())],
                                  env=kenv, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return kenv


def encode(kenv, scope: str | None = None) -> dict:
    cmd = [PY, "-m", "encoder_substrate", "--store", f"dbname={SDB}"] + (["--scope", scope] if scope else [])
    out = subprocess.run(cmd, check=True, env=kenv, cwd=ROOT, capture_output=True, text=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def wait_applied(kdb_admin):
    for _ in range(120):
        n = kdb_admin.execute("select count(*) from kernel.change_set where status not in ('applied', 'invalid', 'rejected', 'conflict')").fetchone()[0]
        if n == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("change sets did not apply: is the validator running?")


# ------------------------------------------------------------------------------------------- the comparisons
def main():
    kenv = build()
    kadmin = psycopg.connect(dbname=KDB, autocommit=True)
    summary = encode(kenv)
    wait_applied(kadmin)
    bad = kadmin.execute("select count(*) from kernel.change_set where status in ('invalid', 'rejected', 'conflict')").fetchone()[0]
    check("encoder-substrate: every change set applied", bad == 0 and summary["new"] > 0,
          f"{summary['new']} submitted, {bad} not applied")

    st = psycopg.connect(dbname=SDB, autocommit=True, row_factory=dict_row)
    rd = psycopg.connect(url("kk_authenticator", PW, KDB), autocommit=True, row_factory=dict_row)
    rd.execute("set role kkm_view_substrate")        # the reader is the module's own role, with the kernel's RLS
    q = lambda c, sql, *a: c.execute(sql, a).fetchall()  # noqa: E731

    ents = {e["id"]: e for e in q(st, "select * from store.entity")}
    iri = {}
    for e in ents.values():
        v = item_iri(e) if e["kind"] == "item" else PROPERTIES.get(e["key"])
        if v:
            iri[e["id"]] = v
    items = sorted((e["id"] for e in ents.values() if e["kind"] == "item"), key=lambda i: int(i[1:]))
    plain = [i for i in items if ents[i]["key"] not in ITEMS]          # urn:kk:substrate:<Qid>, labels in the claims
    sid = {v: k for k, v in iri.items()}
    diffs: dict[str, list[str]] = {}                                   # documented differences, with what was seen

    def note(kind: str, what: str):
        diffs.setdefault(kind, []).append(what)

    # ---- items and labels -------------------------------------------------------------------------------
    kents = {r["iri"]: r for r in q(rd, "select * from substrate.entity")}
    miss = [i for i in plain if iri[i] not in kents]
    wrong = [i for i in plain if iri[i] in kents and (kents[iri[i]]["label"], kents[iri[i]]["description"],
             kents[iri[i]]["aliases"]) != (ents[i]["label"], ents[i]["description"], sorted(ents[i]["aliases"]))]
    check(f"entity: label, description and aliases of the {len(plain)} items", not miss and not wrong, f"{miss} {wrong}")
    pack = [i for i in iri if i not in plain]
    for i in pack:
        k = kents.get(iri[i])
        if k is None:
            note("pack term not in substrate.entity", f"{ents[i]['key']}")
        elif k["label"] != ents[i]["label"]:
            note("pack label differs from the store's", f"{ents[i]['label']!r} -> {k['label']!r}")
    check(f"entity: the {len(pack)} pack terms Substrate's store has are all found",
          not diffs.get("pack term not in substrate.entity"), str(diffs.get("pack term not in substrate.entity")))

    # ---- usable -----------------------------------------------------------------------------------------
    def obj_key(row):
        if row["value_entity"]:
            return ("e", iri.get(row["value_entity"]))
        lit = literal(ents[row["property"]]["datatype"], row["value"])
        return ("l", lit["@value"] if lit else None)

    want, want_rank = set(), {}
    for r in q(st, "select u.*, p.key as pkey from store.usable u join store.entity p on p.id = u.property"):
        if ents[r["subject"]]["kind"] == "item" and r["subject"] in plain and r["property"] in iri and r["snak"] == "value":
            k = (iri[r["subject"]], iri[r["property"]], obj_key(r))
            want.add(k)
            want_rank[k] = r["rank"]
    got, got_rank = set(), {}
    for r in q(rd, "select * from substrate.usable where subject like 'urn:kk:substrate:%%' and predicate not in "
                   "(substrate.term('label'), substrate.term('description'), substrate.term('alt'))"):
        o = ("e", r["object_entity"]) if r["object_entity"] else ("l", r["object_value"]["@value"])
        k = (r["subject"], r["predicate"], o)
        got.add(k)
        got_rank[k] = r["rank"]
    n_pack_st = q(st, "select count(*) as n from store.usable u join store.entity e on e.id = u.subject where e.kind = 'item'")[0]["n"] - len(want)
    note("the packs' own items carry no claims from the encoder (their statements come with the packs)", f"{n_pack_st} statements")
    check(f"usable: the same {len(want)} subject, property, value triples as store.usable", want == got,
          f"only store {sorted(want - got, key=str)[:3]}; only kernel {sorted(got - want, key=str)[:3]}")
    check("usable: the same ranks (preferred or normal)", all(want_rank[k] == got_rank.get(k) for k in want))
    pref = q(st, "select count(*) as n from (select subject, property from store.usable group by 1, 2 having bool_or(rank = 'preferred') "
                 "and bool_or(rank = 'normal')) x")[0]["n"]
    check("usable: where both a preferred and a normal value exist only the preferred is usable",
          pref == 0 and any(v == "preferred" for v in want_rank.values()))

    # ---- maps and alternatives ----------------------------------------------------------------------------
    edges_n = nodes_n = maps = alts = 0
    map_bad, alt_bad = [], []
    for whole in items:
        for depth in (1, 2, 3, 6):
            se = {(iri[s], iri[t], k, stmt is not None, fl) for s, t, k, stmt, fl in
                  [tuple(r.values()) for r in q(st, "select source, target, kind, statement, flagged from store.map(%s, %s)", whole, depth)]}
            ke = {(s, t, k, stmt is not None, fl) for s, t, k, stmt, fl in
                  [tuple(r.values()) for r in q(rd, "select source, target, kind, statement, flagged from substrate.map(%s, %s)", iri[whole], depth)]}
            sn = {(iri[i], la, d, k) for i, la, d, k in
                  [tuple(r.values()) for r in q(st, "select id, label, description, kind from store.map_nodes(%s, %s)", whole, depth)]}
            kn = {tuple(r.values()) for r in q(rd, "select id, label, description, kind from substrate.map_nodes(%s, %s)", iri[whole], depth)}
            # a pack term (single-value constraint, a verdict) has its description in the pack, not in these claims
            if any(d != d2 for (a, _, d, _) in sn for (b, _, d2, _) in kn if a == b and a not in {iri[i] for i in plain}):
                note("a pack term's description is not in the claims", f"{whole}")
            sn = {(i, la, d if i in {iri[x] for x in plain} else None, k) for i, la, d, k in sn}
            kn = {(i, la, d if i in {iri[x] for x in plain} else None, k) for i, la, d, k in kn}
            if se != ke or sn != kn:
                map_bad.append((whole, depth, sorted(se ^ ke, key=str)[:3], sorted(sn ^ kn, key=str)[:3]))
            if se:
                maps += 1
                edges_n += len(se)
                nodes_n += len(sn)
        sa = {(iri[r["entity"]], r["label"], r["fills_now"], tuple(sorted(iri[w] for w in (r["in_whole"] or "").split(", ") if w)),
               tuple(sorted(iri[p] for p in r["provides"]))) for r in q(st, "select * from store.alternatives(%s)", whole)}
        ka = {(r["entity"], r["label"], r["fills_now"], tuple(sorted(w for w in (r["in_whole"] or "").split(", ") if w)),
               tuple(sorted(r["provides"]))) for r in q(rd, "select * from substrate.alternatives(%s)", iri[whole])}
        if sa != ka:
            alt_bad.append((whole, sorted(sa ^ ka, key=str)[:2]))
        alts += bool(sa)
    kinds = {r["kind"] for w in items for r in q(rd, "select kind from substrate.map(%s, 6)", iri[w])}
    check(f"map and map_nodes: the same edges and nodes for {len(items)} items at depths 1, 2, 3 and 6 "
          f"({maps} non-empty maps, {edges_n} edges, {nodes_n} nodes)", not map_bad, str(map_bad[:2]))
    check("map: all five edge kinds occur in the fixture", kinds == {"has-part", "plays", "requires", "provides", "could-fill"}, str(kinds))
    check(f"alternatives: the same candidates, fills_now, wholes and interfaces for {len(items)} roles "
          f"({alts} with candidates)", not alt_bad, str(alt_bad[:2]))
    check("map: flagged is false (the watcher has judged nothing)",
          not any(r["flagged"] for w in items for r in q(rd, "select flagged from substrate.map(%s, 6)", iri[w])))

    # ---- search -------------------------------------------------------------------------------------------
    queries = ["postgres", "pg", "pgvector", "vector", "sql", "queue", "storage", "latency", "Q14", "q16", "work", "nearest",
               "pgrst", "qdrant", "faiss", "interface", "role", "xyzzy", "parser", "docker", "gateway", "index", "a", "candidate",
               "has part", "message queue", "pg_vector", "postgre"]
    s_bad, hits, order_diff, prose_only = [], 0, 0, 0
    for text in queries:
        # compared on Substrate's items: a pack term's page and its extra terms (backstage's) are the packs' to say
        s = [iri[r["id"]] for r in q(st, "select id from store.search(%s, 100)", text) if r["id"] in plain]
        k = [r["iri"] for r in q(rd, "select iri from substrate.search(%s, 100)", text) if r["iri"] in sid and sid[r["iri"]] in plain]
        hits += len(s)
        extra = [x for x in s if x not in k]
        # what the store finds that the claims cannot: the words of an item's prose, which is not in the claims
        unexplained = [x for x in extra if not re.search(re.escape(text.lower()), (ents[sid[x]]["prose"] or "").lower())
                       and not q(st, "select 1 from store.page where entity_id = %s and tsv @@ websearch_to_tsquery('simple', %s)", sid[x], text)]
        if unexplained or [x for x in k if x not in s]:
            s_bad.append((text, unexplained[:3], [x for x in k if x not in s][:3]))
        prose_only += bool(extra)
        order_diff += [x for x in s if x in k] != k
    if prose_only:
        note("search: the store also finds items by words of its page that the claims lack (prose, a parser run, lower ranks)", f"{prose_only} queries")
    if order_diff:
        note("search: the order differs where the page text differs (prose in the store's)", f"{order_diff} queries")
    check(f"search: every hit of the claims is a hit of the store, and each the store has more is found by its prose, "
          f"for {len(queries)} queries ({hits} store hits; {prose_only} queries with prose-only hits, {order_diff} with another order)",
          not s_bad, str(s_bad[:3]))
    check("search: a score is the best of label, alias and page rank (same as the store's for a label hit)",
          all(abs(a["score"] - b["score"]) < 1e-6 for a, b in zip(q(st, "select score from store.search('postgres', 3)"),
                                                                    q(rd, "select score from substrate.search('postgres', 3)"))))

    # ---- pages --------------------------------------------------------------------------------------------
    klabel = {i: kents[iri[i]]["label"] for i in iri if iri[i] in kents}
    kshort = {i: re.sub(r"^.*[#/:]", "", iri[i]) if not iri[i].startswith("urn:kk:substrate:") else iri[i][17:] for i in iri}
    repl = {f"{ents[i]['label']} ({i})": f"{klabel[i]} ({kshort[i]})" for i in iri if i not in plain and i in klabel}

    def parse(md: str):
        head, groups, refs, hidden, cur, order = [], {}, {}, None, None, []
        sect = "head"
        for line in md.split("\n"):
            if line == "## Statements":
                sect = "st"
            elif line == "## References":
                sect = "refs"
            elif "statement(s) at a lower rank" in line:
                hidden = line
            elif sect == "head" and line:
                head.append(line)
            elif sect == "st":
                m = re.fullmatch(r"\*\*(.*)\*\* \((.*)\)", line)
                if m:
                    cur = (m.group(1), m.group(2))
                    groups[cur] = []
                    order.append(cur)
                elif line.startswith("  - "):
                    groups[cur][-1][1].append(line[4:])
                elif line.startswith("- "):
                    groups[cur].append((line[2:], []))
            elif sect == "refs":
                m = re.fullmatch(r"(\d+)\. (.*)", line)
                if m:
                    refs[m.group(1)] = m.group(2)
        return head, groups, refs, hidden, order

    def canon(md: str, side: str, item: str):
        head, groups, refs, hidden, order = parse(md)
        out = {}
        for (label, pid), stmts in groups.items():
            if side == "store":
                pi = next(i for i in ents if i == pid)
                if pi not in iri:
                    note("a property the encoder has no term for is not on the page", f"{label} ({pid}) of {item}")
                    continue
                label, pid = klabel.get(pi, label), kshort.get(pi, pid)
                if ents[pi]["label"] != klabel.get(pi, ents[pi]["label"]):
                    note("a property's label is the pack's", f"{ents[pi]['label']!r} -> {klabel[pi]!r}")
            norm = []
            for val, quals in stmts:
                marks = re.findall(r"\[(\d+)\]", val)
                val = re.sub(r" \[\d+\]", "", val)
                texts = sorted(re.sub(r" \(run [^)]*\)", "", refs[n]) if side == "store" else refs[n] for n in marks)
                if side == "store":
                    for a, b in repl.items():
                        val = val.replace(a, b)
                    if re.search(r"`[^`]+`", val):
                        note("an external id shows without backticks", val)
                        val = val.replace("`", "")
                    nq = []
                    for x in quals:
                        pl, _, qv = x.partition(": ")
                        pr = next((i for i in ents if ents[i]["label"] == pl and ents[i]["kind"] == "property"), None)
                        nq.append(f"{klabel.get(pr, pl)}: " + next((qv.replace(a, b) for a, b in repl.items() if a in qv), qv))
                    quals = nq
                norm.append((val, tuple(sorted(quals)), tuple(texts)))
            out[(label, pid)] = sorted(norm)
        if side == "store":
            drop = ents[item]["prose"]
            if drop:
                head = [h for h in head if h != drop]
                note("prose is not in the claims (the encoder counts it)", item)
            head = [h for h in head if not h.startswith("A property;")]
        return head, out, hidden, sorted(re.sub(r" \(run [^)]*\)", "", t) if side == "store" else t for t in refs.values())

    p_bad, runs = [], 0
    for i in plain:
        sm, km = q(st, "select store.render(%s) as m", i)[0]["m"], q(rd, "select substrate.page(%s) as m", iri[i])[0]["m"]
        if km is None:
            p_bad.append((i, "no page"))
            continue
        runs += bool(re.search(r"\(run ", sm))
        a, b = canon(sm, "store", i), canon(km, "kernel", i)
        if a != b:
            p_bad.append((i, [(x, y) for x, y in zip(a, b) if x != y][:1]))
    check(f"page: the same content as store.render for {len(plain)} items, line by line after the documented normalisations",
          not p_bad, str(p_bad[:2]))
    sm = q(st, "select store.render(%s) as m", "Q14")[0]["m"]
    check("page: a parser's run is not in the claims (the reference reads without it)", "(run " in sm and "(run " not in
          q(rd, "select substrate.page(%s) as m", iri["Q14"])[0]["m"])
    note("a parser's run is not in the reference", f"{runs} pages")
    pg_all = q(rd, "select substrate.page(%s, true) as m", iri[next(i for i in plain if ents[i]["key"] == "fx:pgvector")])[0]["m"]
    check("page: all ranks shows the preferred, the normal and the deprecated values",
          "(deprecated)" in pg_all and "(preferred)" in pg_all and "pgvector-docs" in pg_all)
    pg_use = q(rd, "select substrate.page(%s) as m", iri[next(i for i in plain if ents[i]["key"] == "fx:pgvector")])[0]["m"]
    check("page: by default only the usable value, with the count of those not shown",
          "pgvector-docs" not in pg_use and "3 statement(s) at a lower rank" in pg_use)
    check("page: an IRI nothing is said about has none", q(rd, "select substrate.page('urn:kk:substrate:Q9999') as m")[0]["m"] is None)

    # ---- no security definer; views are security invoker ----------------------------------------------------
    definers = q(rd, "select proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'substrate' and p.prosecdef")
    check("no function in schema substrate is security definer", not definers, str(definers))
    views = q(rd, "select c.relname, c.reloptions from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                  "where n.nspname = 'substrate' and c.relkind = 'v'")
    check(f"all {len(views)} views are security_invoker", views and all("security_invoker=true" in (v["reloptions"] or []) for v in views))
    check("the schema holds no table", not q(rd, "select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                                             "where n.nspname = 'substrate' and c.relkind in ('r', 'p')"))

    # ---- row-level security ---------------------------------------------------------------------------------
    scope = "urn:kk:scope:vs-private"
    kadmin.execute("insert into kernel.scope(iri, label) values (%s, 'view-substrate test: private')", (scope,))
    for role, w in (("kkm_encoder_substrate", True), ("kkm_validator", False)):
        kadmin.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, %s)", (scope, role, w))
    sys.path.insert(0, SUBSTRATE)
    from store import Store, person
    with psycopg.connect(dbname=SDB, autocommit=True) as sc:
        s = Store(sc, actor="fixture-private")
        with s.edit("fixture: something private"):
            pete = person("pete")
            vault = s.item("Zebrafish vault", key="fx:zebrafish", description="a secret whole", aliases=["zfv"])
            part = s.item("Zebrafish part", key="fx:zebrafish-part")
            s.say(vault, s.id("has-part"), part, by=pete, qualifiers=[(s.id("object-has-role"), s.id("fx:storage"))])
            s.say(vault, s.id("described-at-url"), "https://secret.example/vault", by=pete)
    sec = encode(kenv, scope)
    wait_applied(kadmin)
    n_sec = kadmin.execute("select count(*) from api.claims where scope = %s", (scope,)).fetchone()[0]
    check("rls: the private items are claims in a private scope", sec["new"] == 2 and n_sec > 0, f"{sec['new']} change sets, {n_sec} claims")
    v_iri = "urn:kk:substrate:" + next(i for i, k in ents_now(st).items() if k == "fx:zebrafish")

    def blind():
        return (q(rd, "select * from substrate.search('zebrafish')"), q(rd, "select * from substrate.usable where subject = %s", v_iri),
                q(rd, "select substrate.page(%s) as m", v_iri)[0]["m"], q(rd, "select * from substrate.map(%s)", v_iri),
                q(rd, "select * from substrate.entity where iri = %s", v_iri), q(rd, "select * from substrate.wholes where iri = %s", v_iri))

    s1, u1, p1, m1, e1, w1 = blind()
    check("rls: a reader not cleared for the scope finds nothing in search, usable, page, map, entity or wholes",
          not s1 and not u1 and p1 is None and not m1 and not e1 and not w1, f"{len(s1)} {len(u1)} {p1 and p1[:30]} {len(m1)}")
    check("rls: the reader still sees the default scope", len(q(rd, "select * from substrate.search('postgres')")) > 0)
    kadmin.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, 'kkm_view_substrate', true, false)", (scope,))
    s2, u2, p2, m2, e2, w2 = blind()
    check("rls: cleared for it, the same reader finds them", len(s2) >= 1 and len(u2) >= 2 and p2 and "Zebrafish vault" in p2 and
          len(m2) >= 2 and e2 and w2, f"{len(s2)} {len(u2)} {len(m2)}")
    kadmin.execute("delete from kernel.scope_grant where scope = %s and db_role = 'kkm_view_substrate'", (scope,))
    s3, u3, p3, m3, e3, w3 = blind()
    check("rls: and when the grant is taken away they are gone again", not s3 and not u3 and p3 is None and not m3)

    # ---- report ---------------------------------------------------------------------------------------------
    print("\ndocumented differences seen (kind: count, first example):")
    for k, v in sorted(diffs.items()):
        print(f"  {k}: {len(v)}  e.g. {v[0]}")
    print(f"\nequivalence: {len(items)} items ({len(plain)} urn:kk:substrate items, {len(pack)} pack terms), "
          f"{len(want)} usable statements, {maps} maps / {edges_n} edges / {nodes_n} nodes, {alts} role lookups with candidates, "
          f"{len(queries)} searches / {hits} hits, {len(plain)} pages")


def ents_now(conn) -> dict:
    return {r["id"]: r["key"] for r in conn.execute("select id, key from store.entity").fetchall()}


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
    print(f"\n{checks - len(fails)}/{checks} checks passed" + (f"; failed: {fails}" if fails else ""))
    sys.exit(1 if fails else 0)
