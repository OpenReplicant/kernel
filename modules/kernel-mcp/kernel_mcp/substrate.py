"""The substrate profile's tools (D-105): what Substrate's own store answers over MCP, answered from the kernel.

Substrate (its `store/mcp.py`) gives its agent ten tools over a Wikibase-model store: search, read, expand,
locate, path, alternatives, violations, create_item, say and deprecate. Here they have the same names and arguments and
answer in the same layout, from the claims encoder-substrate writes (D-103) through the read model view-substrate
makes (D-104, schema `substrate`). Nothing here touches a table:

  reading   only `substrate.*` and `api.claims` / `api.claim_references`, as the caller's own role, so the
            kernel's row-level security applies; every answer adds the claim handles and their belief
  writing   one change set per call, through `api.submit_change_set`, with the claim shapes encoder-substrate
            makes (this module builds them with the encoder's own `Encoder`, so a statement written here and one
            exported from Substrate's store are equal claims; the author is the calling agent, the reference an
            `interpreter` one, the evidence type testimony)

The tools are registered under `substrate__<name>` (the kernel's own `search` is another tool) and shown to the
substrate profile under Substrate's names (server.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from typing import Annotated, Any
from urllib.parse import unquote

from mcp.server.mcpserver import Context
from psycopg.types.json import Jsonb
from pydantic import Field

from .briefing import handle
from .memory import resolve as claim_of
from .server import PREFIX, READ_ONLY, WRITES, KernelError, connection, mcp, one

SCOPE = os.environ.get("KK_SUBSTRATE_SCOPE", "urn:kk:scope:default")
WAIT = float(os.environ.get("KK_SUBSTRATE_WAIT", "15"))    # seconds a write waits for the validator to apply it
LABELS = ["http://www.w3.org/2000/01/rdf-schema#label", "http://purl.org/dc/terms/description",
          "http://www.w3.org/2004/02/skos/core#altLabel"]
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
REFERENCE = re.compile(r"^urn:kk:substrate:reference:([^?]*)\?by=([^:]*):([^&]*)(?:&version=(.*))?$")
UNSET = "unknown"
# The datatype of each of Substrate's properties (store/sql/001, 003), which decides how `say` reads a value.
# A test compares this with Substrate's own store.
DATATYPE = {
    "instance-of": "item", "subclass-of": "item", "has-part": "item", "object-has-role": "item",
    "described-at-url": "url", "point-in-time": "time", "property-constraint": "item", "format-as-regex": "string",
    "class": "item", "item-of-property-constraint": "item", "reason-for-deprecated-rank": "string",
    "requires-interface": "item", "provides-interface": "item", "for-role": "item", "catalog-id": "external-id",
    "in-catalog": "item", "has-goal": "item", "metric": "string", "target": "quantity", "measurement": "quantity",
    "for-goal": "item", "verdict": "item", "against": "item",
}


# what each tool does to the store (MCP tool annotations, so a router can gate a write and let a read through)
SUBSTRATE_WRITES = {"create_item", "say", "deprecate"}


def tool(name: str, description: str):
    return mcp.tool(name=PREFIX + name, description=description,
                    annotations=WRITES if name in SUBSTRATE_WRITES else READ_ONLY)


def encode():
    try:
        from encoder_substrate import encode as module
    except ImportError:
        raise KernelError("the substrate profile needs the encoder_substrate package (pip install knowledge-kernel)") from None
    return module


def short(iri: str) -> str:
    return iri[len("urn:kk:substrate:"):] if iri.startswith("urn:kk:substrate:") else re.split(r"[#/:]", iri)[-1]


def cite(claim_id, belief) -> str:
    return f"{handle(claim_id)} belief " + (f"{float(belief):.2f}" if belief is not None else "-")


async def fetch(conn, statement: str, params: tuple | dict = ()) -> list[tuple]:
    return await (await conn.execute(statement, params)).fetchall()


# ------------------------------------------------------------------------------------------------ names
async def resolve(conn, ref: Any, properties: bool = False) -> str:
    """An IRI from what Substrate's agent writes: Q13 (an item), a key (has-part, role: its pack term), a
    Wikidata P-number, an IRI, or an item's exact label. Properties are by key (Substrate's own P numbers are
    not kept in the kernel)."""
    if not isinstance(ref, str) or not ref.strip():
        raise KernelError("an id, key or IRI is needed")
    ref, e = ref.strip(), encode()
    if re.fullmatch(r"[Qq][1-9]\d*", ref) and not properties:
        return e.ITEM + ref.upper()
    if re.fullmatch(r"[Pp][1-9]\d*", ref):
        return e.WDT + ref.upper()
    table = e.PROPERTIES if properties else {**e.ITEMS, **e.PROPERTIES}
    if ref in table:
        return table[ref]
    if ":" in ref and " " not in ref:
        return ref
    found = [r[0] for r in await fetch(conn, "select iri from substrate.entity where lower(label) = lower(%s)"
                                             " order by iri limit 6", (ref,))]
    if len(found) > 1:
        raise KernelError(f"{ref!r} names more than one item: {', '.join(short(i) for i in found)}; give an id")
    if not found:
        raise KernelError(f"no entity {ref!r}: give an id (Q13), a key, an IRI or an item's exact label")
    return found[0]


async def item(conn, ref: Any) -> str:
    """An item that exists: a pack item, or one of Substrate's with something said about it."""
    e = encode()
    iri = await resolve(conn, ref)
    if iri in e.ITEMS.values():
        return iri
    if not iri.startswith(e.ITEM):
        raise KernelError(f"{ref!r} is not an item of Substrate's")
    if not (await fetch(conn, "select 1 from api.claims where subject = %s and redacted_at is null limit 1", (iri,))):
        raise KernelError(f"no entity {ref!r}")
    return iri


def item_row(iri: str) -> dict:
    """An item as the encoder's rows have it: a pack item by its key, the rest by their Q id."""
    e = encode()
    key = next((k for k, v in e.ITEMS.items() if v == iri), None)
    return {"id": key or iri[len(e.ITEM):], "kind": "item", "datatype": None, "key": key}


# ------------------------------------------------------------------------------------------------ reading
@tool("search", "Find entities by label, alias, key or the words on their pages. Returns ids (Q items, P properties) "
                "with labels and descriptions, and the label claim's handle and belief.")
async def search(ctx: Context, query: str, limit: int | None = None) -> str:
    async with connection(ctx) as conn:
        hits = await fetch(conn, """
            select e.id, e.label, e.description, u.id, u.belief
            from substrate.search(%(q)s, %(n)s) e
            left join lateral (select x.id, x.belief from substrate.usable x
                               where x.subject = e.iri and x.predicate = %(label)s limit 1) u on true""",
                           {"q": query, "n": max(1, min(int(limit or 10), 100)), "label": LABELS[0]})
    return "\n".join(f"{i}  {label}" + (f": {d}" if d else "") + (f"  [{cite(c, b)}]" if c else "")
                     for i, label, d, c, b in hits) or "nothing found"


@tool("read", "Read an entity's page: its usable statements (preferred, else normal rank), qualifiers, numbered "
              "references and what is flagged. all_ranks shows deprecated ones too. Ends with the claims the page "
              "rests on, each with its handle and belief.")
async def read(ctx: Context, id: str, all_ranks: bool = False) -> str:
    async with connection(ctx) as conn:
        iri = await resolve(conn, id)
        page = await one(conn, "select substrate.page(%s, %s)", (iri, bool(all_ranks)))
        if page is None:
            return f"no entity {id}"
        claims = await fetch(conn, """
            select c.id, substrate.label(c.predicate), substrate.fmt_value(c.object_entity, c.object_value),
                   c.status, c.belief, (select f.reason from substrate.flag f where f.statement_id = c.id)
            from api.claims c
            where c.subject = %(e)s and c.redacted_at is null and not (c.predicate = any(%(labels)s))
              and (case when %(all)s then c.status in ('normal', 'preferred', 'deprecated')
                        else c.id in (select u.id from substrate.usable u where u.subject = %(e)s) end)
            order by 2, c.recorded_at, c.id limit 300""", {"e": iri, "labels": LABELS, "all": bool(all_ranks)})
    lines = [f"- {cite(c, b)}  {p}: {' '.join(str(v).split())[:80]}" + ("" if st == "normal" else f" ({st})")
             + (f" **flagged: {why}**" if why else "") for c, p, v, st, b, why in claims]
    return page.rstrip("\n") + ("\n\n## Claims\n\n" + "\n".join(lines) + "\n" if lines else "\n")


@tool("expand", "One hop from an entity: its usable statements, and the statements whose value it is. Each line ends "
                "with the claim's handle and belief.")
async def expand(ctx: Context, id: str) -> str:
    async with connection(ctx) as conn:
        iri = await resolve(conn, id)
        rows = await fetch(conn, """
            select 'out', u.id, u.predicate, substrate.label(u.predicate), substrate.fmt_value(u.object_entity, u.object_value),
                   u.rank, u.belief
            from substrate.usable u where u.subject = %(e)s and not (u.predicate = any(%(labels)s))
            union all
            select 'in', u.id, u.predicate, substrate.label(u.predicate),
                   format('%%s (%%s)', substrate.label(u.subject), substrate.short(u.subject)), u.rank, u.belief
            from substrate.usable u where u.object_entity = %(e)s and not (u.predicate = any(%(labels)s))
            order by 1 desc, 4, 5""", {"e": iri, "labels": LABELS})
    me = short(iri)
    return "\n".join((f"{me} {pl} ({short(p)}) {v}" if d == "out" else f"{v} {pl} ({short(p)}) {me}")
                     + ("" if rank == "normal" else f" ({rank})") + f"  [{cite(c, b)}]"
                     for d, c, p, pl, v, rank, b in rows) or "no statements"


@tool("locate", "The evidence behind an entity's statements, or one statement (by its claim id or #handle): who stated "
                "it, the source and quoted text it rests on, and each claim's belief. `flagged` is why the span watcher "
                "doubts a claim (its span moved, changed or is gone; null when it does not), with where the text went "
                "(`moved_to`, `found_at`). What the kernel does not keep (a span's hash, a parser's run, a rule's "
                "premises) is left out.")
async def locate(ctx: Context, target: str) -> str:
    async with connection(ctx) as conn:
        t = (target or "").strip()
        by_claim = t.startswith("#") or bool(UUID.fullmatch(t))
        where, param = ("c.id = %(t)s::uuid", await claim_of(conn, t)) if by_claim else \
            ("c.subject = %(t)s and not (c.predicate = any(%(labels)s))", await resolve(conn, t))
        rows = await fetch(conn, f"""
            select c.id, substrate.label(c.subject) || ' ' || substrate.label(c.predicate) || ' '
                         || substrate.fmt_value(c.object_entity, c.object_value),
                   c.status, c.belief, r.source_uri, r.quote, r.span,
                   (select f.reason from substrate.flag f where f.statement_id = c.id),
                   (select f.details -> 'changed' from substrate.flag f where f.statement_id = c.id)
            from api.claims c join api.claim_references r on r.claim_id = c.id
            where {where} and c.redacted_at is null
            order by c.recorded_at, c.id, r.source_uri""", {"t": param, "labels": LABELS})
    out = []
    for cid, says, rank, belief, uri, quote, span, why, changed in rows:
        ref = {"claim": str(cid), "handle": handle(cid), "belief": belief, "statement": str(cid), "says": says,
               "rank": rank, "flagged": why}
        m = REFERENCE.match(uri)
        if m:
            ref.update(reference=m.group(1), by=m.group(2), name=unquote(m.group(3)),
                       version=unquote(m.group(4)) if m.group(4) else None)
        else:
            ref.update(by=None, name=uri)
        if span:
            ref["span"] = {"source": span.get("source"), "locator": span.get("value"), "text": quote}
            # What the watcher found at this span: the store's `changed_since`, and where the text went.
            hit = next((x for x in changed or [] if x.get("source") == span.get("source")
                        and x.get("locator") == span.get("value")), None)
            ref["span"]["changed_since"] = hit is not None
            if hit:
                ref["span"].update({k: hit[k] for k in ("why", "moved_to", "found_at") if k in hit})
        out.append(ref)
    return json.dumps(out, indent=1, default=str) if out else "no evidence found"


@tool("path", "The shortest path from one entity to another along statements that are not deprecated, in their "
              "direction (has part, provides interface, ...). Ends with the claims it follows.")
async def path(ctx: Context, from_: str, to: str) -> str:
    async with connection(ctx) as conn:
        a, b = await resolve(conn, from_), await resolve(conn, to)
        seen: dict[str, tuple | None] = {a: None}
        frontier = [a]
        for _ in range(12):
            if b in seen or not frontier:
                break
            rows = await fetch(conn, """
                select c.subject, c.object_entity, c.id, c.belief, substrate.label(c.predicate)
                from api.claims c
                where c.subject = any(%s) and c.object_entity is not null and c.status in ('normal', 'preferred')
                  and c.redacted_at is null and not (c.predicate = any(%s))
                  and (c.valid_from is null or c.valid_from <= now()) and (c.valid_to is null or c.valid_to > now())
                order by c.subject, c.object_entity, c.id""", (frontier, LABELS))
            frontier = []
            for s, o, cid, belief, pl in rows:
                if o not in seen:
                    seen[o] = (s, cid, belief, pl)
                    frontier.append(o)
        if b not in seen:
            return "no path"
        chain, node = [], b
        while node is not None:
            chain.append(node)
            node = seen[node][0] if seen[node] else None
        chain.reverse()
        labels = dict(await fetch(conn, "select x, substrate.label(x) from unnest(%s::text[]) x", (chain,)))
    steps = []
    for i, n in enumerate(chain):
        via = f"[{seen[n][3]}] " if i else ""
        steps.append(f"{via}{labels[n]} ({short(n)})")
    claims = ", ".join(cite(seen[n][1], seen[n][2]) for n in chain[1:])
    return " -> ".join(steps) + (f"\nclaims: {claims}" if claims else "")


@tool("alternatives", "What could fill a role: everything that provides every interface the role requires, and whether "
                      "it fills the role now and in which whole. Each line ends with the handles of the claims that "
                      "say it provides what the role requires.")
async def alternatives(ctx: Context, role: str) -> str:
    async with connection(ctx) as conn:
        iri = await resolve(conn, role)
        rows = await fetch(conn, """
            select a.entity, a.label, a.fills_now, a.in_whole,
                   (select coalesce(jsonb_agg(jsonb_build_array(u.id, u.belief) order by u.object_entity), '[]')
                    from substrate.usable u
                    where u.subject = a.entity and u.predicate = substrate.term('provides')
                      and u.object_entity in (select r.object_entity from substrate.usable r
                                              where r.subject = %(role)s and r.predicate = substrate.term('requires')))
            from substrate.alternatives(%(role)s) a""", {"role": iri})
    return "\n".join(f"{short(e)}  {label}"
                     + (f"  (fills it now, in {', '.join(short(w) for w in whole.split(', '))})" if fills else "")
                     + (f"  [{', '.join(cite(i, b) for i, b in claims)}]" if claims else "")
                     for e, label, fills, whole, claims in rows) \
        or "nothing provides every interface this role requires (or it requires none)"


@tool("violations", "Constraint violations (reported, never blocking): a property's values of the wrong class, a "
                    "single-value property with several, a string off its format, a value not among those a property "
                    "allows. For one entity, or all. Each line ends with the claim's handle and belief.")
async def violations(ctx: Context, id: str | None = None) -> str:
    async with connection(ctx) as conn:
        iri = await resolve(conn, id) if id else None
        rows = await fetch(conn, """
            select v.subject, v.property, v.kind, v.message, v.statement_id, c.belief
            from substrate.violations v join api.claims c on c.id = v.statement_id
            where %(e)s::text is null or v.subject = %(e)s
            order by v.subject, v.property, v.kind, v.message, v.statement_id""", {"e": iri})
    return "\n".join(f"{short(s)} {short(p)} ({k}): {m} [statement {sid}, {cite(sid, b)}]"
                     for s, p, k, m, sid, b in rows) or "none"


# ------------------------------------------------------------------------------------------------ writing
def time_value(v: str) -> dict:
    """'2026', '2026-11' or '2026-11-17' as Substrate's time value (its store's time_value)."""
    m = re.fullmatch(r"(-?)(\d{1,16})(?:-(\d\d)(?:-(\d\d))?)?", v)
    if not m:
        raise KernelError(f"not a date: {v!r}")
    sign, y, mo, d = m.groups()
    return {"time": f"{'-' if sign else '+'}{y.zfill(4)}-{mo or '00'}-{d or '00'}T00:00:00Z",
            "precision": 11 if d else 10 if mo else 9}


def property_key(ref: Any) -> str:
    """Substrate's key of a property: its key, its pack term's IRI, or a Wikidata P number."""
    e = encode()
    if isinstance(ref, str):
        ref = ref.strip()
        if ref in e.PROPERTIES:
            return ref
        if re.fullmatch(r"[Pp][1-9]\d*", ref):
            ref = e.WDT + ref.upper()
        for k, v in e.PROPERTIES.items():
            if ref == v:
                return k
    raise KernelError(f"unknown property {ref!r}: properties are made by a migration or a person, not here; "
                      f"use a key such as {', '.join(list(e.PROPERTIES)[:6])}")


async def snak(conn, key: str, value: Any, ents: dict) -> dict:
    """A value in the form of Substrate's store rows, for the encoder: an item by its id, the rest as stored."""
    dt = DATATYPE.get(key, "item")
    if dt == "item":
        row = item_row(await item(conn, value))
        ents[row["id"]] = row
        return {"snak": "value", "value": None, "value_entity": row["id"]}
    if dt in ("string", "external-id", "url"):
        if not isinstance(value, str) or not value.strip():
            raise KernelError(f"{key} takes a non-empty string")
        if dt == "url" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*:\S+", value):
            raise KernelError(f"{key} takes a URL: {value!r}")
        return {"snak": "value", "value": value, "value_entity": None}
    if dt == "time":
        if isinstance(value, dict) and isinstance(value.get("time"), str):
            return {"snak": "value", "value": value, "value_entity": None}
        if not isinstance(value, str):
            raise KernelError(f"{key} takes a time such as '2026-11-17'")
        return {"snak": "value", "value": time_value(value), "value_entity": None}
    if isinstance(value, dict):   # a quantity
        extra = set(value) - {"amount"}
        if extra:
            raise KernelError(f"a quantity's {', '.join(sorted(extra))} is not kept in the kernel (D-103): give the amount")
        value = value.get("amount")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KernelError(f"{key} takes a number or {{amount}}")
    return {"snak": "value", "value": {"amount": value}, "value_entity": None}


def client(ctx: Context) -> tuple[str, str]:
    """The name and version the client gave at initialize (what Substrate's own server records)."""
    try:
        info = ctx.session.client_params.client_info
        return str(info.name or "mcp-client"), str(info.version or UNSET)
    except Exception:  # noqa: BLE001  stateless HTTP keeps no session
        return "mcp-client", UNSET


def check_evidence(evidence: Any) -> dict | None:
    if evidence is None:
        return None
    if not isinstance(evidence, dict) or not all(isinstance(evidence.get(k), str) and evidence[k].strip()
                                                 for k in ("source", "locator", "quote")):
        raise KernelError("evidence needs source (a URI), locator (where in it) and quote (the text)")
    return evidence


async def statement_op(conn, ctx: Context, subject: str, prop: Any, value: Any, qualifiers: list | None,
                       evidence: dict | None) -> dict:
    """The add_claim op for one statement, built by the encoder from the rows Substrate's store would hold."""
    e = encode()
    key = property_key(prop)
    ents: dict = {}
    subj = item_row(subject)
    ents[subj["id"]] = subj
    ents[f"P:{key}"] = {"id": f"P:{key}", "kind": "property", "key": key, "datatype": DATATYPE.get(key, "item")}
    st = {"id": "new", "subject": subj["id"], "property": f"P:{key}", "rank": "normal",
          **await snak(conn, key, value, ents), "qualifiers": []}
    for i, q in enumerate(qualifiers or []):
        if not isinstance(q, dict) or "property" not in q or "value" not in q:
            raise KernelError("a qualifier is {property, value}")
        qk = property_key(q["property"])
        ents[f"P:{qk}"] = {"id": f"P:{qk}", "kind": "property", "key": qk, "datatype": DATATYPE.get(qk, "item")}
        st["qualifiers"].append({"ord": i, "property": f"P:{qk}", **await snak(conn, qk, q["value"], ents)})
    name, version = client(ctx)
    ident = json.dumps([subject, key, value, qualifiers or [], evidence, name], sort_keys=True, default=str)
    st["references"] = [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, ident)), "by_kind": "interpreter", "by_name": name,
                         "by_version": version if version != UNSET else None, "run": None,
                         "span": {"source": evidence["source"], "locator": evidence["locator"],
                                  "text": evidence["quote"]} if evidence else None}]
    enc = e.Encoder(list(ents.values()))
    op = enc.claim(st)
    if op is None:
        raise KernelError("that cannot be said here: " + "; ".join(f"{f['finding']} {f.get('property') or f.get('key') or ''}"
                                                                 for f in enc.findings.list()))
    return op


async def settle(conn, cs_id: str, run: str) -> tuple[str, list[str]]:
    """Wait for the validator to apply the change set; its claim ids when it has. Refusals raise."""
    status, report = "submitted", None
    deadline = time.monotonic() + WAIT
    while True:
        row = await fetch(conn, "select status, validation_report from api.change_sets where id = %s::uuid", (cs_id,))
        status, report = row[0] if row else ("submitted", None)
        if status in ("applied", "invalid", "rejected", "conflict") or time.monotonic() > deadline:
            break
        await asyncio.sleep(0.2)
    if status in ("invalid", "rejected", "conflict"):
        await conn.execute("select api.finish_run(%s::uuid, 'failed', '{}'::jsonb)", (run,))
        raise KernelError(f"the kernel refused it ({status}): {json.dumps(report, default=str)[:600]}")
    if status != "applied":
        return status, []
    await conn.execute("select api.finish_run(%s::uuid, 'succeeded', '{}'::jsonb)", (run,))
    return status, [str(r[0]) for r in await fetch(conn, "select id from api.claims where change_set = %s::uuid"
                                                         " order by recorded_at, id", (cs_id,))]


async def write(ctx: Context, tool_name: str, ops: list[dict], scope: str, info: dict, key_extra: str = "") -> tuple[str, str, list[str]]:
    """Submit one change set as the caller, in a run that records the tool and its note; then wait for it."""
    async with connection(ctx) as conn:
        me = await one(conn, "select kernel.my_agent()")
        run = str(await one(conn, "select api.start_run(null, null, '[]'::jsonb, %s)",
                            (Jsonb({"via": "substrate profile", "tool": tool_name, **info}),)))
        digest = hashlib.sha256((me + scope + key_extra + json.dumps(ops, sort_keys=True, default=str)).encode()).hexdigest()
        cs = {"idempotency_key": f"substrate-mcp:{digest[:32]}", "attributed_to": me, "generated_by": run,
              "target_scope": scope, "ops": ops}
        result = await one(conn, "select api.submit_change_set(%s)", (Jsonb(cs),))
    async with connection(ctx) as conn:
        status, claims = await settle(conn, str(result["id"]), run)
    return str(result["id"]), status, claims


def pending(cs: str, status: str) -> str:
    return f"change set {cs} is {status}; its claim id shows in read once the kernel has applied it"


async def new_id(conn) -> str:
    """A fresh Q id. The kernel has no counter (it is not allowed a table for one) and a caller sees only its own
    scopes, so a sequence could hand two agents the same id: microseconds since the epoch cannot."""
    while True:
        qid = f"Q{time.time_ns() // 1000}"
        if not await fetch(conn, "select 1 from api.claims where subject = %s limit 1", (encode().ITEM + qid,)):
            return qid


@tool("create_item", "Make an item (a thing, part, role, interface, goal, catalog, candidate). Search first: it answers "
                     "with similar items that already exist. instance_of is an id or key (role, interface, goal, "
                     "catalog, or any class item). The new id is a long Q number (the kernel keeps no counter).")
async def create_item(ctx: Context, label: str, description: str | None = None, aliases: list[str] | None = None,
                      instance_of: str | None = None,
                      evidence: Annotated[dict | None, Field(description="the text this rests on: {source: the source's "
                                                             "URI, locator: where in it (p3, L10-20), quote: the text}")] = None) -> str:
    label = (label or "").strip()
    if not label:
        raise KernelError("an item needs a label")
    evidence = check_evidence(evidence)
    if evidence and not instance_of:
        raise KernelError("evidence goes with a statement: give instance_of, or say it with say")
    e = encode()
    async with connection(ctx) as conn:
        similar = await fetch(conn, "select id, label from substrate.search(%s, 5) where score >= 0.5", (label,))
        qid = await new_id(conn)
        ops = e.Encoder([]).labels({"id": qid, "kind": "item", "key": None, "label": label,
                                    "description": description or None, "aliases": list(aliases or [])})
        if instance_of:
            target = await item(conn, instance_of)
            ops.append(await statement_op(conn, ctx, e.ITEM + qid, "instance-of", target, None, evidence))
    cs, status, _ = await write(ctx, "create_item", ops, SCOPE, {"label": label})
    note = "".join(f"\nsimilar, already here: {i} {la}" for i, la in similar)
    return f"{qid}{note}" if status == "applied" else f"{qid}\n{pending(cs, status)}{note}"


@tool("say", "State something about an entity: property (key, e.g. has-part), value in its datatype's form (an item's id or "
             "key; a string; a quantity {amount}; a time '2026-11-17'; a URL), qualifiers [{property, value}], and the "
             "evidence it rests on. It enters at normal rank as your claim; the answer is its claim id.")
async def say(ctx: Context, subject: str, property: str, value: Any,
              qualifiers: list[dict] | None = None,
              evidence: Annotated[dict | None, Field(description="the text this rests on: {source: the source's URI, "
                                                     "locator: where in it (p3, L10-20), quote: the text}")] = None,
              note: str | None = None) -> str:
    evidence = check_evidence(evidence)
    async with connection(ctx) as conn:
        subj = await item(conn, subject)
        op = await statement_op(conn, ctx, subj, property, value, qualifiers, evidence)
    cs, status, claims = await write(ctx, "say", [op], SCOPE, {"note": note} if note else {})
    if status != "applied":
        return pending(cs, status)
    return claims[0] if claims else f"change set {cs} applied: the kernel already holds this statement"


@tool("deprecate", "Deprecate a statement you made (by its claim id or #handle), saying why. Someone else's statements "
                   "are reviewed by a person: say a contradicting one instead.")
async def deprecate(ctx: Context, statement: str, reason: str) -> str:
    if not (reason or "").strip():
        raise KernelError("deprecating needs a reason")
    async with connection(ctx) as conn:
        cid = await claim_of(conn, statement)
        row = (await fetch(conn, "select scope, status, attributed_to = kernel.my_agent() from api.claims where id = %s::uuid",
                           (cid,)))[0]
    scope, status, mine = row
    if not mine:
        raise KernelError("someone else also stated this; a person reviews it. Say a contradicting statement instead.")
    if status == "deprecated":
        return f"already deprecated {cid}"
    ops = [{"op": "set_status", "claim": cid, "status": "deprecated", "expect_status": status, "reason": "retracted"}]
    cs, state, _ = await write(ctx, "deprecate", ops, scope, {"claim": cid, "reason": reason.strip()})
    return f"deprecated {cid}" if state == "applied" else pending(cs, state)


TOOLS = ("search", "read", "expand", "locate", "path", "alternatives", "violations", "create_item", "say", "deprecate")
INSTRUCTIONS = ("Substrate's store, answered from the knowledge kernel: entities with statements, references and quoted "
                "evidence. search to find, read a page, expand to neighbours, locate the evidence, path and alternatives "
                "over parts, roles and interfaces, violations for what breaks a property's constraints; create_item and say write your claims, with evidence; deprecate "
                "withdraws one of yours. Every answer shows claim handles (#abcd1234) and their belief: cite them.")
if os.environ.get("KK_PROFILE") == "substrate":   # stdio: the profile is known at start
    mcp._lowlevel_server.instructions = INSTRUCTIONS
