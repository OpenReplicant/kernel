"""Claims as a briefing an agent can read and cite (M6-T1; the context-pack decoder, M6-T7, builds on it).

Pure functions, no database. A briefing is short lines of prose, not triples: labels instead of IRIs,
one line per memory (an as:Note: its kind, its words, who said it), one per other claim. Every line says
who it comes from and how sure the kernel is, and ends with a handle (#1a2b3c4d, the claim id's first
eight characters) that explain(handle) expands to sources, quotes and history. Lines are added in the
order given until the token budget (about four characters a token) is spent; the rest are counted.
"""
from __future__ import annotations

import re

AS = "https://www.w3.org/ns/activitystreams#"
DCT = "http://purl.org/dc/terms/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
NOTE = AS + "Note"
KIND = "urn:kk:memory-kind:"
RECALL_WHEN = "https://w3id.org/knowledge-kernel/ns#recallWhen"
# What a note is made of; recall reads these for every note it finds.
NOTE_PARTS = (RDF_TYPE, AS + "content", AS + "attributedTo", DCT + "type", DCT + "subject")
KINDS = ("fact", "decision", "preference", "commitment", "correction", "conclusion")
HANDLE = 8


def handle(claim_id) -> str:
    return "#" + str(claim_id)[:HANDLE]


def words(iri: str) -> str:
    """An IRI's last segment, for when it has no label."""
    return re.split(r"[/#:]", iri.rstrip("/#:"))[-1] or iri


def name(iri: str | None, labels: dict[str, str]) -> str:
    return labels.get(iri) or words(iri) if iri else ""


def value(c: dict) -> str:
    v = c.get("object_value")
    return str(v.get("@value", v) if isinstance(v, dict) else v)


def notes(parts: list[dict]) -> dict[str, dict]:
    """Per note subject: its content claim, kind, author and topics (from the NOTE_PARTS claims)."""
    out: dict[str, dict] = {}
    typed = {c["subject"] for c in parts if c["predicate"] == RDF_TYPE and c.get("object_entity") == NOTE}
    for c in parts:
        if c["subject"] not in typed:
            continue
        n = out.setdefault(c["subject"], {"about": []})
        p = c["predicate"]
        if p == AS + "content":
            n["content"] = c
        elif p == AS + "attributedTo":
            n["author"] = c["object_entity"]
        elif p == DCT + "type" and (c.get("object_entity") or "").startswith(KIND):
            n["kind"] = c["object_entity"][len(KIND):]
        elif p == DCT + "subject":
            n["about"].append(c["object_entity"])
    return {s: n for s, n in out.items() if "content" in n}


def who(iri: str | None, me: str | None, agents: dict[str, dict], labels: dict[str, str],
        types: dict[str, str]) -> str:
    """Who said it: you, a person, an agent; `agents` are kernel agents, `types` actors from transcripts."""
    if not iri:
        return "unknown"
    if iri == me:
        return "you"
    a = agents.get(iri)
    if a:
        return f"{a['label']} ({'agent' if a['kind'] == 'software' else a['kind']})"
    kind = {AS + "Person": "person", AS + "Application": "agent"}.get(types.get(iri, ""))
    return f"{name(iri, labels)} ({kind})" if kind else name(iri, labels)


def tail(c: dict) -> str:
    belief = "unscored" if c.get("belief") is None else f"belief {float(c['belief']):.2f}"
    mark = (", preferred" if c.get("status") == "preferred" else "") \
        + (", a premise was withdrawn" if c.get("withdrawn") else "")
    return f"{belief}{mark} {handle(c['id'])}"


def lines(claims: list[dict], found: dict[str, dict], labels: dict[str, str], agents: dict[str, dict],
          me: str | None, types: dict[str, str] | None = None) -> list[str]:
    """One line per memory (once, however many of its claims were recalled) and per other claim."""
    types = types or {}
    out, seen = [], set()
    for c in claims:
        n = found.get(c["subject"])
        if n:
            if c["subject"] in seen:
                continue
            seen.add(c["subject"])
            content = n["content"]
            about = ", ".join(name(a, labels) for a in n["about"])
            text = value(content).replace("\n", " ")
            author = who(n.get("author") or content["attributed_to"], me, agents, labels, types)
            premises = n.get("premises") or 0
            out.append(f"- {n.get('kind', 'note')}: \"{text}\"" + (f" (about {about})" if about else "")
                       + (f" (from {premises} premise{'s' if premises > 1 else ''})" if premises else "")
                       + f" — {author}; {tail(content)}")
        else:
            obj = name(c["object_entity"], labels) if c.get("object_entity") else f"\"{value(c)[:300]}\""
            quals = "; ".join(f"{words(k)} {v}" for k, v in (c.get("qualifiers") or {}).items())
            out.append(f"- {name(c['subject'], labels)} · {words(c['predicate'])} · {obj}"
                       + (f" ({quals})" if quals else "")
                       + f" — {who(c['attributed_to'], me, agents, labels, types)}; {tail(c)}")
    return out


def fit(header: str, body: list[str], budget: int) -> str:
    """The header and as many lines as fit in `budget` tokens (about four characters each)."""
    room, kept = budget * 4 - len(header) - 80, []
    for line in body:
        if room - len(line) - 1 < 0 and kept:
            break
        kept.append(line)
        room -= len(line) + 1
    more = len(body) - len(kept)
    return "\n".join([header, *kept] + ([f"({more} more not shown; ask with a larger budget)"] if more else []))


# Memories with a recall cue (D-91) the caller can see: the note's words, kind, author and cue, one row each.
CUED = f"""select coalesce(jsonb_agg(r), '[]') from (
  select c.id, c.subject, c.object_value->>'@value' as text, c.belief, c.status, w.object_value->>'@value' as cue,
         coalesce(substr(k.object_entity, {len(KIND) + 1}), 'note') as kind, coalesce(a.object_entity, c.attributed_to) as by,
         coalesce(ag.label, (select l.object_value->>'@value' from api.claims l where l.subject = coalesce(a.object_entity, c.attributed_to)
                             and l.predicate = '{LABEL}' and l.status <> 'deprecated' limit 1),
                  coalesce(a.object_entity, c.attributed_to)) as label,
         ag.kind as agent_kind
    from api.claims w
    join api.claims c on c.subject = w.subject and c.predicate = '{AS}content' and c.status <> 'deprecated'
    left join lateral (select object_entity from api.claims x where x.subject = w.subject and x.predicate = '{DCT}type'
                         and x.status <> 'deprecated' and starts_with(x.object_entity, '{KIND}') limit 1) k on true
    left join lateral (select object_entity from api.claims x where x.subject = w.subject
                         and x.predicate = '{AS}attributedTo' and x.status <> 'deprecated' limit 1) a on true
    left join kernel.agent ag on ag.iri = coalesce(a.object_entity, c.attributed_to)
   where w.predicate = '{RECALL_WHEN}' and w.status <> 'deprecated'
   order by c.recorded_at desc limit 500) r"""


def cue_line(row: dict, reason: str, me: str | None) -> str:
    """A cued memory as a briefing line, saying which cue brought it up."""
    if row.get("by") == me:
        who = "you"
    elif row.get("agent_kind"):
        who = f"{row['label']} ({'agent' if row['agent_kind'] == 'software' else row['agent_kind']})"
    else:
        who = row.get("label") or "unknown"
    return f"- {row.get('kind', 'note')}: \"{(row.get('text') or '').strip()}\" (cue: {reason}) — {who}; {tail(row)}"
