"""The memory profile's tools (M6-T1): remember, recall, changes_since; explain is shared with operators.

An agent with this profile never builds a change set, picks an IRI or reads a triple. `remember` writes
one sentence as an as:Note attributed to the agent, quoting its words (testimony; it goes through the
validator like any change set). `recall` and `changes_since` answer with a briefing (briefing.py).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import unquote

import anyio
from mcp.server.mcpserver import Context
from psycopg.types.json import Jsonb

from . import briefing, cues, recall as ranked
from .briefing import AS, DCT, KIND, KINDS, LABEL, NOTE, NOTE_PARTS, RDF_TYPE
from .server import CLAIM_COLUMNS, KernelError, connection, expand, mcp, one, rows

SCOPE = os.environ.get("KK_MEMORY_SCOPE", "urn:kk:scope:default")
DATETIME = "http://www.w3.org/2001/XMLSchema#dateTime"
VOCAB = "urn:kk:scope:vocab"
TMS = "urn:kk:module:truth-maintenance"   # the packs' terms: not news to an agent
RUN = """select id from kernel.run where agent = kernel.my_agent() and status = 'running'
         order by started_at desc limit 1"""


async def resolve(conn, h: str) -> str:
    """A claim id from a handle (#1a2b3c4d, any prefix of at least four characters) or a full id."""
    h = h.strip().lstrip("#").lower()
    hexes = h.replace("-", "")
    if len(hexes) < 4 or any(ch not in "0123456789abcdef" for ch in hexes) or len(hexes) > 32:
        raise KernelError(f"not a claim handle: {h!r}")
    lo, hi = (hexes + pad * (32 - len(hexes)) for pad in "0f")
    uuid = lambda x: f"{x[:8]}-{x[8:12]}-{x[12:16]}-{x[16:20]}-{x[20:]}"   # noqa: E731
    found = await one(conn, "select coalesce(array_agg(id::text), '{}') from (select id from api.claims"
                            " where id between %s::uuid and %s::uuid limit 2) c", (uuid(lo), uuid(hi)))
    if not found:
        raise KernelError(f"no claim you can see has the handle #{h}")
    if len(found) > 1:
        raise KernelError(f"#{h} matches more than one claim; give more characters")
    return found[0]


async def context(conn, claims: list[dict]) -> tuple[dict, dict, dict, dict, str]:
    """What a briefing needs beyond the claims: notes, labels, kernel agents, actor types, and who asks."""
    subjects = list({c["subject"] for c in claims})
    parts = await one(conn, rows(f"select {CLAIM_COLUMNS} from api.claims where subject = any(%s)"
                                 " and predicate = any(%s) and status <> 'deprecated'"), (subjects, list(NOTE_PARTS)))
    found = briefing.notes(parts)
    counts = await one(conn, "select coalesce(jsonb_object_agg(claim_id, n), '{}') from (select claim_id::text, count(*) n"
                             " from kernel.claim_derivation where claim_id = any(%s::uuid[]) group by claim_id) d",
                       ([n["content"]["id"] for n in found.values()],))
    for n in found.values():
        n["premises"] = counts.get(str(n["content"]["id"]), 0)
    iris = {c["subject"] for c in claims} | {c["object_entity"] for c in claims if c.get("object_entity")} \
        | {c["attributed_to"] for c in claims} | {n.get("author") for n in found.values()} \
        | {a for n in found.values() for a in n["about"]}
    iris = [i for i in iris if i]
    labels = await one(conn, "select coalesce(jsonb_object_agg(subject, label), '{}') from (select distinct on (subject)"
                             " subject, object_value->>'@value' as label from api.claims where subject = any(%s)"
                             " and predicate = %s and status <> 'deprecated' order by subject, belief desc nulls last) l",
                       (iris, LABEL))
    agents = await one(conn, "select coalesce(jsonb_object_agg(iri, jsonb_build_object('kind', kind, 'label', label)),"
                             " '{}') from kernel.agent where iri = any(%s)", (iris,))
    types = await one(conn, "select coalesce(jsonb_object_agg(subject, object_entity), '{}') from api.claims"
                            " where subject = any(%s) and predicate = %s and object_entity = any(%s)"
                            " and status <> 'deprecated'", (iris, RDF_TYPE, [AS + "Person", AS + "Application"]))
    me = await one(conn, "select kernel.my_agent()")
    # Lines whose premises were withdrawn (truth maintenance, D-89): its latest verdict is inconclusive.
    ids = [str(c["id"]) for c in claims] + [str(n["content"]["id"]) for n in found.values()]
    flagged = set(await one(conn, """select coalesce(array_agg(claim_id::text), '{}') from (
        select distinct on (claim_id) claim_id, verdict from api.verifications where method = %s
           and claim_id = any(%s::uuid[]) order by claim_id, recorded_at desc) v where verdict = 'inconclusive'""",
                            (TMS, ids)) if ids and await one(conn, "select to_regclass('api.verifications') is not null")
                  else [])
    for c in [*claims, *(n["content"] for n in found.values())]:
        c["withdrawn"] = str(c["id"]) in flagged
    return found, labels, agents, types, me


def slug(text: str) -> str:
    return "-".join("".join(ch if ch.isalnum() else " " for ch in text.lower()).split())[:80] or "topic"


async def subject(conn, about: str) -> tuple[str, str | None]:
    """What a memory is about: (IRI, None), or (a new topic's IRI, its name) when nothing has that name.
    A #handle means its claim's subject; an IRI or CURIE is taken as it is; a name is looked up by label."""
    if about.startswith("#"):
        return await one(conn, "select subject from api.claims where id = %s::uuid", (await resolve(conn, about),)), None
    if ":" in about and " " not in about:
        return expand(about), None
    found = await one(conn, "select coalesce(array_agg(distinct subject), '{}') from (select subject from api.claims"
                            " where predicate = %s and lower(object_value->>'@value') = lower(%s)"
                            " and status <> 'deprecated' limit 6) l", (LABEL, about))
    if len(found) > 1:
        raise KernelError(f"{about!r} names more than one thing: {', '.join(found[:5])}; give one of these IRIs")
    return (found[0], None) if found else (f"urn:kk:topic:{slug(about)}", about)


# The scope a conclusion goes in: the default memory scope if every premise may be read wherever it may
# (invariant 3), else the first premise scope that is at least as narrow as all of them and writable.
CONCLUSION_SCOPE = """select t from unnest(%(cands)s::text[]) with ordinality as c(t, n)
  where kernel.role_can_write(current_user, t)
    and not exists (select 1 from api.claims p where p.id = any(%(ids)s::uuid[])
                    and not kernel.readers_subset(t, p.scope))
  order by n limit 1"""


@mcp.tool()
async def remember(ctx: Context, kind: str, text: str, about: str | None = None, replaces: str | None = None,
                   because: list[str] | None = None, when: dict | None = None) -> dict:
    """Remember one thing, in a sentence. kind: fact, decision, preference, commitment, correction or
    conclusion. about: what it concerns (optional): a name ("the Quokka ledger": the entity with that label,
    or a new topic), an IRI or CURIE, or a #handle (that claim's subject). replaces: the #handle of a memory
    this one corrects (the old one is kept, marked superseded). because: the #handles it follows from
    (required for a conclusion): it is recorded as derived from them, kept as private as the most private of
    them, and flagged if one of them is later withdrawn. when: a recall cue, so it comes back whatever the
    ranking: {"always": true} (every session start), {"mentions": ["Sam"]}, {"on": "--05-30", "before": 7}
    (a date; --MM-DD yearly), {"where": "deploy/*"} (a path), {"tool": "Bash(git push*)"} (before that action).
    It is recorded as yours, quoting your words; recall shows it once the kernel has applied it."""
    text = " ".join(text.split())
    if kind not in KINDS:
        raise KernelError(f"kind must be one of {', '.join(KINDS)}")
    if not text or len(text) > 2000:
        raise KernelError("text must be one to 2000 characters: one sentence is best")
    try:
        cue = cues.check(when) if when else None
    except ValueError as e:
        raise KernelError(f"when: {e}") from None
    if kind == "conclusion" and not because:
        raise KernelError("a conclusion needs because: the #handles of the claims it follows from")
    async with connection(ctx) as conn:
        me = await one(conn, "select kernel.my_agent()")
        about, topic = await subject(conn, about.strip()) if about and about.strip() else (None, None)
        premises = sorted({await resolve(conn, h) for h in because or []})
        scope = SCOPE
        if premises:
            scopes = await one(conn, "select coalesce(array_agg(distinct scope), '{}') from api.claims"
                                     " where id = any(%s::uuid[])", (premises,))
            scope = await one(conn, CONCLUSION_SCOPE, {"cands": [SCOPE, *sorted(scopes)], "ids": premises})
            if not scope:
                raise KernelError("no scope you may write is at least as private as all of these premises (every "
                                  "reader of the conclusion's scope needs its own grant on each premise's scope)")
        run = await one(conn, RUN) or await one(conn, "select api.start_run(null, null, '[]'::jsonb, %s)",
                                                  (Jsonb({"via": "remember"}),))
        digest = hashlib.sha256("\x1f".join([me, kind, text, about or "", *premises,
                                             json.dumps(cue, sort_keys=True) if cue else ""]).encode()).hexdigest()
        note = f"urn:kk:memory:{digest[:24]}"
        ref = {"source_uri": f"urn:kk:run:{run}", "quote": text}
        facts = [(RDF_TYPE, {"entity": NOTE}), (AS + "content", {"value": {"@value": text}}),
                 (AS + "attributedTo", {"entity": me}), (DCT + "type", {"entity": KIND + kind}),
                 (AS + "published", {"value": {"@value": datetime.now(timezone.utc).isoformat(), "@type": DATETIME}}),
                 *([(DCT + "subject", {"entity": about})] if about else []),
                 *([(briefing.RECALL_WHEN, {"value": {"@value": json.dumps(cue, sort_keys=True)}})] if cue else [])]
        named = [(about, LABEL, {"value": {"@value": topic}})] if topic else []
        ops = [{"op": "add_claim", "ref": f"c{i}", "subject": note, "predicate": p, "object": o,
                "evidence_type": "testimony", "references": [ref]} for i, (p, o) in enumerate(facts)]
        if premises:   # the words themselves are the inference; the rest of the note is the agent's testimony
            ops[1].update(evidence_type="inferred", derived_from=premises)
        ops += [{"op": "add_claim", "subject": s, "predicate": p, "object": o, "evidence_type": "testimony",
                 "references": [{"source_uri": f"urn:kk:run:{run}", "quote": topic}]} for s, p, o in named]
        if replaces:
            old = await resolve(conn, replaces)
            # A memory is replaced whole (every claim of its note); any other claim, alone.
            prior = await one(conn, rows("""select c.id, c.predicate, c.status from api.claims c, api.claims o
                where o.id = %(c)s::uuid and c.status <> 'deprecated' and c.subject = o.subject
                  and (c.id = o.id or exists (select 1 from api.claims t where t.subject = o.subject
                       and t.predicate = %(type)s and t.object_entity = %(note)s and t.status <> 'deprecated'))"""),
                              {"c": old, "type": RDF_TYPE, "note": NOTE})
            by = {op["predicate"]: op["ref"] for op in ops}
            ops += [{"op": "set_status", "claim": c["id"], "status": "deprecated", "expect_status": c["status"],
                     "reason": "superseded", "replaced_by": by.get(c["predicate"], "c1")} for c in prior]
        key = "memory:" + hashlib.sha256(f"{digest}|{replaces or ''}".encode()).hexdigest()[:32]
        cs = {"idempotency_key": key, "attributed_to": me, "generated_by": str(run), "target_scope": scope, "ops": ops}
        result = await one(conn, "select api.submit_change_set(%s)", (Jsonb(cs),))
    return {"memory": note, "change_set": result["id"], "status": result["status"],
            "note": "recall shows it once it is applied" if result["status"] != "applied" else "applied"}


@mcp.tool()
async def recall(ctx: Context, question: str, budget: int = 800) -> str:
    """What the kernel knows about the question, as a briefing within `budget` tokens (default 800): one
    line per memory or claim, saying who said it and its belief (0 to 1), ending in a #handle. Cite the
    handles you rely on; explain(handle) gives a line's sources and history."""
    budget = max(100, min(budget, 8000))
    async with connection(ctx) as conn:
        indexed = bool(await one(conn, ranked.INSTALLED) and await one(conn, ranked.READY))
        model = await one(conn, ranked.DENSE) if indexed else None
        vec = await anyio.to_thread.run_sync(ranked.vector, question, model) if model else None
        limit = max(10, budget // 25)
        args = ranked.params(question, ranked.pool(2 * limit) if indexed else 2 * limit, vec)
        found = await one(conn, ranked.ranked(indexed, vec), args)
        if found and indexed and ranked.reranker():
            texts = await one(conn, ranked.TEXTS, {"subjects": ranked.subjects(found)})
            found = await anyio.to_thread.run_sync(ranked.rerank, question, found, limit, texts)
        found = found or await one(conn, ranked.SUBSTRING, ranked.params(question, limit)) or []
        # The packs' term definitions answer an operator's search, not an agent's memory question.
        found = [c for c in found if c.get("scope") != VOCAB][:limit]
        notes, labels, agents, types, me = await context(conn, found)
        cued = [(r, why) for r in await one(conn, briefing.CUED)
                if (cue := cues.parse(r["cue"])) and (why := cues.fires(cue, text=question))]
    if not found and not cued:
        return f'Nothing recalled for "{question}".'
    first = {r["subject"] for r, _ in cued}
    return briefing.fit(f'Recalled for "{question}" (who said it; belief 0-1; explain(#handle) for sources):',
                        [briefing.cue_line(r, why, me) for r, why in cued]
                        + briefing.lines([c for c in found if c["subject"] not in first], notes, labels, agents, me,
                                         types), budget)


@mcp.tool()
async def changes_since(ctx: Context, since: str | None = None, budget: int = 800) -> str:
    """What others changed since you last looked: claims added and claims superseded or retracted, newest
    first, as a briefing within `budget` tokens. since: the cursor a previous call returned (an ISO time);
    by default, the end of your last finished run (else the last day). Ends with the next cursor."""
    budget = max(100, min(budget, 8000))
    async with connection(ctx) as conn:
        start = await one(conn, "select coalesce(%s::timestamptz, (select max(ended_at) from kernel.run"
                                " where agent = kernel.my_agent()), now() - interval '1 day')", (since,))
        cursor = await one(conn, "select now()")
        added = await one(conn, rows(f"select {CLAIM_COLUMNS} from api.claims where recorded_at > %(t)s"
                                     " and attributed_to is distinct from kernel.my_agent() and scope <> %(v)s"
                                     " and status <> 'deprecated' order by recorded_at desc limit 200"),
                          {"t": start, "v": VOCAB})
        ended = await one(conn, rows(f"""select {', '.join('c.' + x for x in CLAIM_COLUMNS.split(', '))},
                                             s.reason, s.replaced_by from kernel.claim_status s
                                        join api.claims c on c.id = s.claim_id
                                        where s.recorded_at > %(t)s and s.status = 'deprecated' and c.scope <> %(v)s
                                        order by s.recorded_at desc limit 100"""), {"t": start, "v": VOCAB})
        notes, labels, agents, types, me = await context(conn, added + ended)
    body = briefing.lines(added, notes, labels, agents, me, types)
    old = briefing.notes(ended)   # a superseded memory is one line, not one per claim of its note
    gone, seen = [], set()
    for c in ended:
        if c["subject"] in old:
            if c["subject"] in seen:
                continue
            seen.add(c["subject"])
            c = old[c["subject"]]["content"]
        mark = f"[{c['reason'] or 'deprecated'}" + (f" by {briefing.handle(c['replaced_by'])}" if c.get("replaced_by") else "")
        gone += [line.replace("- ", f"- {mark}] ", 1) for line in briefing.lines([c], old, labels, agents, me, types)]
    header = (f"Changes since {start.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC: {len(body)} added,"
              f" {len(gone)} superseded or retracted"
              f" (next cursor: {cursor.isoformat()}).")
    return briefing.fit(header, body + gone, budget) if body or gone else header


# The same briefings as MCP resources, for harnesses that attach resources to the context (M6-T1).
@mcp.resource("kernel://recall/{question}{?budget}", name="recall", mime_type="text/plain",
              description="What the kernel knows about the question, as a cited briefing (recall).")
async def recall_resource(question: str, ctx: Context, budget: int = 800) -> str:
    return await recall(ctx, unquote(question), budget)


@mcp.resource("kernel://changes{?since}", name="changes", mime_type="text/plain",
              description="What others changed since you last looked, as a cited briefing (changes_since).")
async def changes_resource(ctx: Context, since: str | None = None) -> str:
    return await changes_since(ctx, unquote(since) if since else None)
