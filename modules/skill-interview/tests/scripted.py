"""The scripted interview (fixtures/acme.yaml): the change sets an agent following the interview skill
submits after each of the person's answers. Shared by the gap processor's unit test and the end-to-end
check (scripts/e2e_interview.py)."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

FIX = Path(__file__).resolve().parent / "fixtures"
SHORT = {"ORG:": "http://www.w3.org/ns/org#", "BBO:": "https://www.irit.fr/recherches/MELODI/ontologies/BBO#",
         "prov:": "http://www.w3.org/ns/prov#"}
WORDS = {"a": "http://www.w3.org/1999/02/22-rdf-syntax-ns#type", "label": "http://www.w3.org/2000/01/rdf-schema#label"}


def iri(term: str, script: dict) -> str:
    local = {"org:": script["organisation"] + ":", "person:": "urn:kk:person:" + script["source"] + ":",
             "proc:": script["process"] + ":"}
    if term in WORDS:
        return WORDS[term]
    for short, long in {**SHORT, **local}.items():
        if term.startswith(short):
            return long + term[len(short):]
    return term


def gap(entry: str, script: dict) -> tuple[str, str]:
    kind, target = entry.split()
    return "urn:kk:gap-kind:" + kind, iri(target, script)


def load(source: str | None = None) -> tuple[list[dict], dict, list[dict]]:
    """(messages of acme.jsonl, the script, per answer {message IRI, text, ops, closes, open});
    `source` overrides the script's (the e2e check uses a fresh one per run)."""
    script = yaml.safe_load((FIX / "acme.yaml").read_text())
    messages = [json.loads(line) for line in (FIX / "acme.jsonl").read_text().splitlines() if line.strip()]
    text = {m["id"]: m["text"] for m in messages}
    src = source or script["source"]
    out = []
    for a in script["answers"]:
        message = f"urn:kk:message:{src}:{messages[0]['conversation']}:{a['message']}"
        ops = []
        for s, p, o, quote in a["claims"]:
            if quote not in text[a["message"]]:
                raise ValueError(f"the quote {quote!r} is not in message {a['message']}")
            obj = {"value": {"@value": o[1:-1]}} if o.startswith('"') else {"entity": iri(o, script)}
            ops.append({"op": "add_claim", "subject": iri(s, script), "predicate": iri(p, script), "object": obj,
                        "evidence_type": "extracted", "references": [{"source_uri": message, "quote": quote}]})
        out.append({"message": message, "id": a["message"], "text": text[a["message"]], "ops": ops,
                    "closes": {gap(g, script) for g in a.get("closes", [])},
                    "open": {gap(g, script) for g in a.get("open", [])}})
    return messages, script, out
