"""One answer in an interview -> the statements it makes about the organisation and its processes.

A model proposes facts (subject, relation, object, each with its kind, and the words that say so); code
keeps only what it can check, because no method verifies its own output:

- the relation exists and its subject and object are of the kinds it joins (vocab.RELATIONS);
- the quote occurs in the answer, word for word (case and spacing aside), not paraphrased;
- each name is in the answer, or names something already on the map, or is the speaker ("I", "me");
- a person where a post belongs ("Sam runs finance") stands for the one post that person holds.

Pure functions; the model is called by the pipeline (Cognee's LLM settings) or read from an answers file.
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel

from .vocab import KINDS, RELATIONS

SELF = {"i", "me", "my", "myself", "we", "us", "our"}
# Kinds a model may call by another name.
SYNONYMS = {"team": "unit", "department": "unit", "group": "unit", "job": "post", "role": "post", "position": "post",
            "company": "organisation", "organization": "organisation", "activity": "step", "task": "step"}


class Fact(BaseModel):
    subject: str
    subject_kind: str
    relation: str
    object: str
    object_kind: str
    quote: str


class Facts(BaseModel):
    facts: list[Fact] = []


PROMPT = """You map how an organisation works from an interview. From the person's answer, list the facts
it states, as JSON: {"facts": [{"subject", "subject_kind", "relation", "object", "object_kind", "quote"}]}.

Kinds: organisation, unit (a team or department), post (a job: "buyer", "finance director"), person,
process, step (a piece of work, named as a short verb phrase: "check the budget code").
Relations:
%s
Rules:
- quote: the answer's exact words that state the fact, copied, not paraphrased.
- Use the names already on the map when the answer means the same thing.
- "I" and "me" are the speaker. A post is a job, never a person's name.
- Only facts the answer states; nothing from the question alone. No facts: {"facts": []}."""


def prompt(text: str, speaker: str, question: str, known: list[str]) -> tuple[str, str]:
    relations = "\n".join(f"- {r}: {d[3]} ({'/'.join(sorted(d[0]))} -> {'/'.join(sorted(d[1]))})"
                          for r, d in RELATIONS.items())
    user = (f"Already on the map:\n{chr(10).join(known) or '(nothing yet)'}\n\n"
            f"Question: {question or '(none)'}\nSpeaker: {speaker}\nAnswer: {text}")
    return PROMPT % relations, user


def parse(answer: str) -> list[dict]:
    """The facts in a model's answer: JSON, possibly fenced or with text around it."""
    m = re.search(r"\{.*\}", answer or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return []
    facts = data.get("facts") if isinstance(data, dict) else None
    return [f for f in facts or [] if isinstance(f, dict)]


def norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", re.sub(r"[^\w' ]+", " ", s)).strip()


STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "our", "my", "its", "post", "role", "position", "job"}


def grounded(name: str, said: str) -> bool:
    """Every word of the name is in the answer, allowing endings ("raise" in "raises")."""
    words = [w for w in norm(name).split() if w not in STOP]
    stems = {w[:5] for w in said.split()}
    return bool(words) and all(w[:5] in stems for w in words)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def label(kind: str, name: str) -> str:
    name = re.sub(r"^(the |a |an |our |my )?((post|role|position|job) of (the |a )?)?", "", " ".join(name.split()), flags=re.I)
    return name if kind in ("person", "organisation") else name[:1].upper() + name[1:].lower()


class Map:
    """What is on the map so far: entities by key, and who holds which post."""

    def __init__(self):
        self.entities: dict[str, dict] = {}   # "kind:slug" -> {kind, name}
        self.holds: dict[str, set[str]] = {}  # person key -> post keys

    def key(self, kind: str, name: str) -> str:
        return f"{kind}:{slug(label(kind, name))}"

    def lines(self) -> list[str]:
        return [f"- {e['kind']}: {e['name']}" for e in self.entities.values()]

    def find(self, kind: str, name: str, speaker: str) -> str | None:
        """The key of a known entity this name means: exact, the speaker, or a person's first name."""
        n = norm(name)
        if n in SELF or n == norm(speaker):
            return self.key("person", speaker)
        k = self.key(kind, name)
        if k in self.entities:
            return k
        if kind == "person":
            hits = [k for k, e in self.entities.items() if e["kind"] == "person" and norm(e["name"]).split()[:1] == n.split()[:1]]
            if len(hits) == 1:
                return hits[0]
        return None

    def add(self, s: dict):
        for kind, name, key in ((s["subject_kind"], s["subject"], s["s"]), (s["object_kind"], s["object"], s["o"])):
            self.entities.setdefault(key, {"kind": kind, "name": label(kind, name)})
        if s["relation"] == "holds":
            self.holds.setdefault(s["s"], set()).add(s["o"])


def check(facts: list[dict], text: str, speaker: str, known: Map) -> tuple[list[dict], list[dict]]:
    """(statements kept, facts dropped with the reason). Statements carry keys `s` and `o` on the map."""
    kept, dropped, said = [], [], norm(text)

    def drop(f, why):
        dropped.append({**f, "why": why})

    for f in facts:
        f = {k: str(f.get(k) or "").strip() for k in ("subject", "subject_kind", "relation", "object", "object_kind", "quote")}
        r = RELATIONS.get(f["relation"])
        sk, ok = (SYNONYMS.get(k.lower(), k.lower()) for k in (f["subject_kind"], f["object_kind"]))
        if not r or sk not in KINDS or ok not in KINDS:
            drop(f, "unknown relation or kind")
            continue
        if not f["quote"] or norm(f["quote"]) not in said:
            drop(f, "quote not in the answer")
            continue
        ends = []
        for kind, name, allowed in ((sk, f["subject"], r[0]), (ok, f["object"], r[1])):
            key = known.find(kind, name, speaker)
            if key and norm(name) in SELF | {norm(speaker)}:
                kind, name = "person", speaker
            elif key:
                kind = known.entities[key]["kind"]
            if kind == "person" and "post" in allowed and key and len(known.holds.get(key, ())) == 1:
                key = next(iter(known.holds[key]))   # the one post this person holds
                kind, name = "post", known.entities[key]["name"]
            if kind not in allowed:
                ends = None
                break
            if key is None:
                if not grounded(name, said):
                    ends = None
                    break
                key = known.key(kind, name)
            ends.append((kind, known.entities.get(key, {}).get("name") or label(kind, name), key))
        if not ends:
            drop(f, "a name not in the answer, or of the wrong kind")
            continue
        (sk, sn, s), (ok, on, o) = ends
        if s == o:
            drop(f, "relates a thing to itself")
            continue
        st = {"subject": sn, "subject_kind": sk, "s": s, "relation": f["relation"], "object": on, "object_kind": ok, "o": o,
              "quote": f["quote"]}
        if any((k["s"], k["relation"], k["o"]) == (s, f["relation"], o) for k in kept):
            continue
        kept.append(st)
        known.add(st)
    return kept, dropped
