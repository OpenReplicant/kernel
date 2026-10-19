"""Memory extraction (M6-T4): one message in, the memories it holds out. Pure; no database, no network.

A model reads one message (with the message before it, for context) and the earlier memories it might
update, and answers a JSON array. Each item becomes a memory, the same as:Note shape as `remember` and the
encoder's inline lines (D-88): as:content (one sentence that stands alone), as:attributedTo who wrote the
message, dcterms:type its kind, dcterms:isPartOf the message, as:context the conversation, as:published,
dcterms:subject what it is about (a topic) and kk:recallWhen a cue (D-91), each claim quoting the words of
the message it rests on (evidence `extracted`). A quote must be in the message, or the quote verifier would
refute it: when the model gives none that is, the sentence of the message sharing most words with the
memory is quoted. A memory sharing no word with the message is dropped (a small model repeats memories from
other messages). An item that replaces an earlier memory supersedes that memory's claims.
"""
from __future__ import annotations

import hashlib
import json
import re

AS = "https://www.w3.org/ns/activitystreams#"
DCT = "http://purl.org/dc/terms/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
RECALL_WHEN = "https://w3id.org/knowledge-kernel/ns#recallWhen"
DATETIME = "http://www.w3.org/2001/XMLSchema#dateTime"
KIND = "urn:kk:memory-kind:"
KINDS = ("fact", "preference", "decision", "commitment", "correction")

PROMPT = """You keep the long-term memory of an assistant. Read the MESSAGE below (sent {when} by {author}) and \
list what is worth remembering for later conversations:
- facts about the people: what they have, did, bought, plan, where, when, how many, who they know
- their preferences and tastes
- decisions, with the reason if one is given
- commitments: who will do what, by when
- corrections of something said earlier
When the assistant wrote the message, keep only its commitments (what it will do).

Rules:
- Take memories only from the MESSAGE. The PREVIOUS message is context, to understand short answers.
- The EARLIER MEMORIES are already kept: never repeat them. Use them only for "replaces".
- "text": one plain sentence that stands alone. Name who it is about ("The user", or a name). Turn relative \
dates into dates, counting from {when}.
- "quote": the exact words of the MESSAGE it rests on, copied character for character, on one line, short.
- "kind": fact, preference, decision, commitment or correction.
- "replaces": the number of an EARLIER MEMORY this one updates or corrects (a new value, a change of mind), \
else null.
- "about": a short name of the person, thing or project it concerns, else null.
- "on": a date YYYY-MM-DD for an event still ahead (a birthday, a deadline), else null.
- "always": true only for a standing instruction to the assistant ("always...", "never..."), else false.
Skip small talk, general knowledge and the assistant's generic advice. Asking a question or for help is \
not a memory; what the question reveals about the person is (a pet's name, a trip, a purchase). Return only a JSON array; \
[] if there is nothing to remember.

EARLIER MEMORIES:
{earlier}

PREVIOUS ({previous_author}):
{previous}

MESSAGE ({author}):
{text}

JSON array:"""


def prompt(text: str, author: str, when: str, previous: str = "", previous_author: str = "",
           earlier: list[str] | None = None) -> str:
    listed = "\n".join(f"{i}. {m}" for i, m in enumerate(earlier or [], 1)) or "(none)"
    return PROMPT.format(text=text, author=author or "unknown", when=(when or "")[:10] or "an unknown date",
                         previous=previous or "(none)", previous_author=previous_author or "none",
                         earlier=listed)


def parse(answer: str) -> list[dict]:
    """The items of the model's JSON array (the first one in the answer); the complete objects of an array
    that was cut off; [] when there is none."""
    answer = re.sub(r"<think>.*?</think>", "", answer or "", flags=re.S)
    decoder = json.JSONDecoder()
    start = answer.find("[")
    while start >= 0:
        try:
            items, _ = decoder.raw_decode(answer[start:])
            if isinstance(items, list):
                return [i for i in items if isinstance(i, dict)]
        except ValueError:
            pass
        start = answer.find("[", start + 1)
    out, at = [], answer.find("{", max(answer.find("["), 0))
    while at >= 0:
        try:
            item, end = decoder.raw_decode(answer[at:])
            if isinstance(item, dict):
                out.append(item)
            at = answer.find("{", at + end)
        except ValueError:
            break
    return out


def locate(quote: str, text: str) -> str | None:
    """The span of `text` the quote copies: exactly, else the same words ignoring case, spacing and
    punctuation between them, on one line. None when it is not there."""
    quote = " ".join(str(quote or "").split()).strip(" .\"'")
    if len(quote) < 3:
        return None
    for line in text.splitlines():
        if quote in line:
            return quote
    words = re.findall(r"\w+", quote)
    if not words:
        return None
    pattern = re.compile(r"\b" + r"\W+".join(map(re.escape, words)) + r"\b", re.I)
    for line in text.splitlines():
        m = pattern.search(line)
        if m:
            return m.group(0)
    return None


def slug(text: str) -> str:
    return "-".join("".join(ch if ch.isalnum() else " " for ch in text.lower()).split())[:80] or "topic"


DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STANDING = re.compile(r"^\W*(please\s+)?(always|never)\b", re.I)   # "Always run the tests": an instruction


STOP = {"user", "users", "assistant", "will", "would", "their", "they", "them", "about", "that", "this", "with",
        "from", "have", "has", "had", "been", "were", "was", "are", "the", "and", "for", "wants", "said", "says",
        "not", "but", "his", "her", "its", "our", "who"}


def content(sentence: str) -> list[str]:
    return [w for w in re.findall(r"\w+", sentence.lower()) if len(w) >= 3 and w not in STOP and not w.isdigit()]


def best(sentence: str, text: str) -> str | None:
    """The sentence of the message sharing most content words with the memory: its quote when the model
    gave none that is there. None when none shares any."""
    words = content(sentence)
    parts = [p.strip() for line in text.splitlines() for p in re.split(r"(?<=[.!?])\s+", line) if p.strip()]
    scored = [(sum(w[:5] in p.lower() for w in words), -i, p) for i, p in enumerate(parts)]
    top = max(scored, default=(0, 0, None))
    return top[2] if top[0] else None


def grounded(sentence: str, text: str) -> bool:
    """Some content word of the sentence (its first five letters) occurs in the message: a model that pins a
    real quote on a memory from another message is caught here (a paraphrase shares little, but something)."""
    words = content(sentence)
    low = text.lower()
    return not words or any(w[:5] in low for w in words)


NOT_TOPICS = {"user", "the user", "me", "i", "assistant", "the assistant", "you"}


def memories(items: list[dict], text: str, earlier: int, agent: bool = False) -> list[dict]:
    """The usable items: a sentence grounded in the message, a quote found in it, a kind (a standing
    instruction is a preference, another unknown kind a fact); `replaces` within 1..earlier.
    From an agent's message (`agent`), only commitments: the rest of what it said is in the message."""
    out = []
    for i in items:
        kind = str(i.get("kind") or "").strip().lower()
        sentence = " ".join(str(i.get("text") or "").split())
        quote = locate(i.get("quote"), text) or best(sentence, text)
        if not sentence or len(sentence) > 600 or not quote or not grounded(sentence, text):
            continue
        standing = i.get("always") is True or bool(STANDING.match(quote) or STANDING.match(sentence))
        if kind not in KINDS:   # a small model's own word for it ("always", "instruction", "event")
            kind = "preference" if standing else "fact"
        if agent and kind != "commitment":
            continue
        r = i.get("replaces")
        replaces = int(r) if isinstance(r, (int, str)) and str(r).isdigit() and 1 <= int(r) <= earlier else None
        about = " ".join(str(i.get("about") or "").split())[:80] or None
        about = None if about and about.lower() in NOT_TOPICS else about
        cue = {}
        if isinstance(i.get("on"), str) and DAY.match(i["on"].strip()):
            cue["on"] = i["on"].strip()
        if standing:
            cue["always"] = True
        out.append({"kind": kind, "text": sentence, "quote": quote, "replaces": replaces, "about": about,
                    "cue": cue or None})
    return out


def ops(found: list[dict], message: dict, artifact: str | None, replaced: dict[int, list[dict]],
        named: set[str] | frozenset = frozenset()) -> list[dict]:
    """add_claim ops for each memory, quoting the message; a label for each new topic (not in `named`);
    set_status for the claims of each memory it replaces (`replaced`: earlier memory number -> its
    current claims {id, predicate, status})."""
    out: list[dict] = []
    named = set(named)
    for n, m in enumerate(found):
        digest = hashlib.sha256("\x1f".join([message["subject"], m["kind"], m["text"]]).encode()).hexdigest()[:20]
        note = f"urn:kk:memory:extract:{digest}"
        ref = {"quote": m["quote"], **({"artifact": artifact} if artifact else {"source_uri": message["subject"]})}
        topic = f"urn:kk:topic:{slug(m['about'])}" if m["about"] else None
        facts = [(RDF_TYPE, {"entity": AS + "Note"}), (AS + "content", {"value": {"@value": m["text"]}}),
                 (AS + "attributedTo", {"entity": message["author"]}), (DCT + "type", {"entity": KIND + m["kind"]}),
                 (DCT + "isPartOf", {"entity": message["subject"]}),
                 *([(AS + "context", {"entity": message["context"]})] if message.get("context") else []),
                 *([(AS + "published", {"value": {"@value": message["published"], "@type": DATETIME}})]
                   if message.get("published") else []),
                 *([(DCT + "subject", {"entity": topic}), ] if topic else []),
                 *([(RECALL_WHEN, {"value": {"@value": json.dumps(m["cue"], sort_keys=True)}})] if m["cue"] else [])]
        refs = {}
        for i, (p, o) in enumerate(facts):
            refs[p] = f"m{n}c{i}"
            out.append({"op": "add_claim", "ref": refs[p], "subject": note, "predicate": p, "object": o,
                        "evidence_type": "extracted", "references": [ref]})
        if topic and topic not in named:
            named.add(topic)
            out.append({"op": "add_claim", "subject": topic, "predicate": LABEL, "object": {"value": {"@value": m["about"]}},
                        "evidence_type": "extracted", "references": [ref]})
        for c in replaced.get(m["replaces"], []) if m["replaces"] else []:
            out.append({"op": "set_status", "claim": c["id"], "status": "deprecated", "expect_status": c["status"],
                        "reason": "superseded", "replaced_by": refs.get(c["predicate"], refs[AS + "content"])})
    return out
