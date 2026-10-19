"""Interaction transcripts -> claims (M5-T3, D-81). Pure functions; no database.

Two inputs, read into one list of events (a message or an action) per conversation:

  chat JSON lines   one object per line: {"id", "conversation", "author", "kind" (person | agent),
                    "time", "text", "reply_to"} for a message, or {"id", "conversation", "author", "time",
                    "tool", "used": [IRI or path, ...]} for an action (a tool call, a file or API action,
                    a device command)
  claude-code       a Claude Code session transcript (~/.claude/projects/*/<session>.jsonl): the person's
                    and the agent's text, and each tool call with the files or URLs it used (thinking and
                    tool results are left out)

The events are rendered as a plain-text transcript, which is the artifact every claim cites: a message's
claims quote its text, an action's claims quote its line, so the quote verifier can check them (JSON
escapes would keep a quote from occurring in the source file). Claims (deterministic):

  message   urn:kk:message:<source>:<conversation>:<id>  a as:Note; as:content, as:attributedTo the actor,
            as:context the conversation, as:published, as:inReplyTo the message before it
  action    urn:kk:action:<source>:<conversation>:<id>   a prov:Activity; as:actor, as:instrument the tool,
            prov:used each file or resource, as:context, as:published
  actor     urn:kk:actor:<source>:<name>   a as:Person or as:Application; rdfs:label
  tool      urn:kk:tool:<name>             a as:Service; rdfs:label
  conversation urn:kk:conversation:<source>:<conversation>  a as:OrderedCollection; rdfs:label
  memory    urn:kk:memory:<source>:<conversation>:<id>:<n>  a line of a message that starts with Decision:,
            Remember:, Preference:, Commitment: or Correction: (M6-T1): an as:Note with the rest of the line as
            as:content, as:attributedTo who wrote the message, dcterms:type its kind (urn:kk:memory-kind:decision,
            ...; Remember is a fact), dcterms:isPartOf the message; it quotes the whole line
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

AS = "https://www.w3.org/ns/activitystreams#"
PROV = "http://www.w3.org/ns/prov#"
DCT = "http://purl.org/dc/terms/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
DATETIME = "http://www.w3.org/2001/XMLSchema#dateTime"
QUOTE = 300            # characters of a message quoted (its start, verbatim)
CONTENT = 4000         # characters of a message kept in as:content
PATH_KEYS = ("file_path", "path", "notebook_path", "url")
SKIP_TEXT = re.compile(r"^\s*<(system-reminder|command-|local-command)")
# An inline memory: a line of a message that starts with one of these words and a colon (optionally as a
# list item or in bold), e.g. "Decision: we pin psycopg 3.3.6".
INLINE = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?(Decision|Remember|Preference|Commitment|Correction)(?:\*\*)?:"
                    r"(?:\*\*)?\s+(\S.*?)\s*$")
KINDS = {"Remember": "fact"}


@dataclass
class Event:
    kind: str                     # message | action
    id: str
    conversation: str
    author: str
    person: bool = True
    time: str | None = None
    text: str = ""                # a message's text
    reply_to: str | None = None
    tool: str | None = None       # an action's tool
    used: list[str] = field(default_factory=list)
    line: int = 0                 # the source line (1-based), for the ingest filter's findings


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-") or "x"


def read_chat(lines: list[str]) -> list[Event]:
    out = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        d = json.loads(line)
        common = {"id": str(d["id"]), "conversation": str(d.get("conversation", "default")),
                  "author": str(d["author"]), "person": d.get("kind", "person") == "person",
                  "time": d.get("time"), "line": n}
        if d.get("tool"):
            out.append(Event("action", tool=str(d["tool"]), used=[str(u) for u in d.get("used", [])], **common))
        else:
            out.append(Event("message", text=str(d.get("text", "")), reply_to=d.get("reply_to"), **common))
    return out


def used_by(tool_input: dict, cwd: str | None) -> list[str]:
    """The files and URLs a tool call names: file:<path> (relative to the session's folder when under it)."""
    out = []
    for key in PATH_KEYS:
        v = tool_input.get(key)
        if not isinstance(v, str) or not v:
            continue
        if re.match(r"^https?://", v):
            out.append(v)
        else:
            p = PurePosixPath(v)
            if cwd and p.is_absolute() and str(p).startswith(cwd.rstrip("/") + "/"):
                p = p.relative_to(cwd)
            out.append(f"file:{p}")
    return out


def read_claude_code(lines: list[str], person: str = "person", agent: str = "claude-code") -> list[Event]:
    out = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("type") not in ("user", "assistant") or not isinstance(d.get("message"), dict):
            continue
        content = d["message"].get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
        is_person = d["type"] == "user"
        for i, b in enumerate(blocks):
            common = {"conversation": str(d.get("sessionId", "session")), "time": d.get("timestamp"), "line": n}
            if b.get("type") == "text" and b.get("text", "").strip() and not SKIP_TEXT.match(b["text"]):
                out.append(Event("message", id=f"{d['uuid']}.{i}", author=person if is_person else agent,
                                 person=is_person, text=b["text"], **common))
            elif b.get("type") == "tool_use":
                out.append(Event("action", id=str(b.get("id") or f"{d['uuid']}.{i}"), author=agent, person=False,
                                 tool=str(b["name"]), used=used_by(b.get("input") or {}, d.get("cwd")), **common))
    return out


def sniff(first: str) -> str:
    d = json.loads(first)
    return "claude-code" if "sessionId" in d or d.get("type") in ("user", "assistant", "summary") else "chat"


def render(events: list[Event]) -> tuple[str, dict[str, tuple[int, int, str]]]:
    """The transcript as plain text, and per event its (first line, last line, quote)."""
    out, where, conv = [], {}, None
    for e in events:
        if e.conversation != conv:
            conv = e.conversation
            out += ([""] if out else []) + [f"# conversation {conv}", ""]
        stamp = f"[{e.time}] " if e.time else ""
        if e.kind == "message":
            body = e.text.splitlines() or [""]
            start = len(out) + 1
            out += [f"{stamp}{e.author}:", *body, ""]
            where[e.id] = (start, start + len(body), e.text.strip()[:QUOTE].strip())
        else:
            line = f"{e.author} used {e.tool}" + (f": {', '.join(e.used)}" if e.used else "")
            out.append(f"{stamp}{line}")
            where[e.id] = (len(out), len(out), line)
    return "\n".join(out) + "\n", where


def encode(events: list[Event], source: str, sha: str) -> list[dict]:
    """The add_claim ops for the events, citing the rendered transcript `sha`."""
    _, where = render(events)
    ops: list[dict] = []
    seen: set[str] = set()

    def claim(s, p, o, ref):
        ops.append({"op": "add_claim", "subject": s, "predicate": p, "object": o, "evidence_type": "deterministic",
                    "references": [ref]})

    def once(iri, kind, label, ref):
        if iri not in seen:
            seen.add(iri)
            claim(iri, RDF_TYPE, {"entity": kind}, ref)
            claim(iri, LABEL, {"value": {"@value": label}}, ref)

    last: dict[str, str] = {}
    for e in events:
        first, final, quote = where[e.id]
        ref = {"artifact": sha, "quote": quote, "span": {"start_line": first, "end_line": final}} if quote \
            else {"artifact": sha, "span": {"start_line": first, "end_line": final}}
        conv = f"urn:kk:conversation:{slug(source)}:{slug(e.conversation)}"
        actor = f"urn:kk:actor:{slug(source)}:{slug(e.author)}"
        once(conv, AS + "OrderedCollection", e.conversation, ref)
        once(actor, AS + ("Person" if e.person else "Application"), e.author, ref)
        when = [(AS + "published", {"value": {"@value": e.time, "@type": DATETIME}})] if e.time else []
        if e.kind == "message":
            me = f"urn:kk:message:{slug(source)}:{slug(e.conversation)}:{slug(e.id)}"
            prev = (f"urn:kk:message:{slug(source)}:{slug(e.conversation)}:{slug(str(e.reply_to))}" if e.reply_to
                    else last.get(e.conversation))
            facts = [(RDF_TYPE, {"entity": AS + "Note"}), (AS + "content", {"value": {"@value": e.text[:CONTENT]}}),
                     (AS + "attributedTo", {"entity": actor}), (AS + "context", {"entity": conv}), *when,
                     *([(AS + "inReplyTo", {"entity": prev})] if prev else [])]
            last[e.conversation] = me
            for n, text in enumerate(e.text.splitlines()):
                m = INLINE.match(text)
                if not m:
                    continue
                at = first + 1 + n   # the message's text starts on the line after its author
                line_ref = {"artifact": sha, "quote": text.strip(), "span": {"start_line": at, "end_line": at}}
                note = f"{me.replace('urn:kk:message:', 'urn:kk:memory:', 1)}:{n}"
                kind = KINDS.get(m.group(1), m.group(1).lower())
                for p, o in [(RDF_TYPE, {"entity": AS + "Note"}), (AS + "content", {"value": {"@value": m.group(2)}}),
                             (AS + "attributedTo", {"entity": actor}), (DCT + "type", {"entity": f"urn:kk:memory-kind:{kind}"}),
                             (DCT + "isPartOf", {"entity": me}), (AS + "context", {"entity": conv}), *when]:
                    claim(note, p, o, line_ref)
        else:
            me = f"urn:kk:action:{slug(source)}:{slug(e.conversation)}:{slug(e.id)}"
            tool = f"urn:kk:tool:{slug(e.tool or 'tool')}"
            once(tool, AS + "Service", e.tool or "tool", ref)
            facts = [(RDF_TYPE, {"entity": PROV + "Activity"}), (AS + "actor", {"entity": actor}),
                     (AS + "instrument", {"entity": tool}), (AS + "context", {"entity": conv}), *when,
                     *[(PROV + "used", {"entity": u}) for u in e.used]]
        for p, o in facts:
            claim(me, p, o, ref)
    return ops


def change_sets(ops: list[dict], artifact: dict, key: str, batch: int, prior: list[dict], agent: str,
                scope: str) -> list[dict]:
    """Change sets of at most `batch` claims keyed `key` + their position, the first carrying the
    artifact; earlier claims (another version of the transcript) are superseded by the claim that
    replaces them, the rest retracted in the last set."""
    from kk.changes import same, supersede
    sets = [{"idempotency_key": f"{key}{i // batch}", "attributed_to": agent, "target_scope": scope,
             "ops": ops[i:i + batch]} for i in range(0, max(len(ops), 1), batch)]
    sets[0]["ops"].insert(0, artifact)
    left = {same(c["subject"], c["predicate"], c["qualifiers"], c["valid_from"]): c for c in prior}
    for cs in sets:
        mine = [left.pop(k) for k in {same(op["subject"], op["predicate"], op.get("qualifiers"), op.get("valid_from"))
                                      for op in cs["ops"] if op["op"] == "add_claim"} if k in left]
        if mine:
            supersede(cs, mine)
    if left:
        supersede(sets[-1], list(left.values()))
    return sets
