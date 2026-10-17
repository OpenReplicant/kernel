"""Papers (JSON lines) -> claims (D-59). Pure functions; no database.

  urn:kk:paper:<source>:<id>  a fabio:ResearchPaper ; dcterms:title ; dcterms:abstract

A line is one paper: an id (`doc_id`, `_id` or `id`), `title`, and `abstract` (text, or a list of
sentences as in SciFact, joined with single spaces). Every claim is deterministic and references the
file artifact with a FragmentSelector naming the paper's id.
"""
from __future__ import annotations

import json
import re

FABIO = "http://purl.org/spar/fabio/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
TITLE, ABSTRACT = "http://purl.org/dc/terms/title", "http://purl.org/dc/terms/abstract"


def paper_iri(source: str, pid) -> str:
    return f"urn:kk:paper:{source}:{re.sub(r'[^A-Za-z0-9._-]+', '-', str(pid)).strip('-')}"


def read(data: bytes) -> list[dict]:
    """[{id, title, abstract}] from JSON lines; abstract as text."""
    papers = []
    for n, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        pid = next((row[k] for k in ("doc_id", "_id", "id") if row.get(k) not in (None, "")), None)
        if pid is None:
            raise ValueError(f"line {n}: no id (doc_id, _id or id)")
        abstract = row.get("abstract", row.get("text", ""))
        if isinstance(abstract, list):
            abstract = " ".join(s.strip() for s in abstract)
        papers.append({"id": str(pid), "title": (row.get("title") or "").strip(), "abstract": abstract.strip()})
    return papers


def encode(papers: list[dict], source: str, sha: str, kind: str = "ResearchPaper") -> list[dict]:
    """The add_claim ops for these papers (the caller adds the artifact op)."""
    ops = []
    for p in papers:
        s = paper_iri(source, p["id"])
        ref = [{"artifact": sha, "span": {"type": "FragmentSelector", "value": p["id"]}}]

        def claim(pred, obj):
            ops.append({"op": "add_claim", "subject": s, "predicate": pred, "object": obj,
                        "evidence_type": "deterministic", "references": ref})
        claim(RDF_TYPE, {"entity": FABIO + kind})
        if p["title"]:
            claim(TITLE, {"value": {"@value": p["title"]}})
        if p["abstract"]:
            claim(ABSTRACT, {"value": {"@value": p["abstract"]}})
    return ops
