"""Does a quote occur in its source? Pure functions, standard library only.

A quote (oa:TextQuoteSelector exact) matches when it occurs in the source text exactly, or after both are
normalised: Unicode NFKC (ligatures, full-width forms), typographic quotes and dashes to ASCII, and every
run of whitespace to one space (a quote copied across a line break). Case and words must match: a
paraphrase is not a quote.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

PUNCT = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                       " ": " ", "­": None})


def normal(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).translate(PUNCT)).strip()


def match(quote: str, source: str) -> str | None:
    """'exact', 'normalised', or None when the quote is not in the source."""
    if quote in source:
        return "exact"
    if normal(quote) and normal(quote) in normal(source):
        return "normalised"
    return None


def read_artifact(store: Path, sha256: str) -> tuple[str | None, str]:
    """An artifact's text from the content-addressed store, or (None, why)."""
    path = store / sha256
    if not re.fullmatch(r"[0-9a-f]{64}", sha256) or not path.is_file():
        return None, "artifact not in this store"
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        return None, "artifact bytes do not match their sha256"
    return data.decode("utf-8", errors="replace"), ""


def read_file(root: Path, uri: str) -> tuple[str | None, str]:
    """A file: source's text, resolved under `root` (never outside it), or (None, why)."""
    if uri.startswith("file://") or not uri.startswith("file:"):
        return None, "not a relative file: URI"
    path = (root / uri[len("file:"):].lstrip("/")).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return None, "file not under the source root"
    return path.read_text(encoding="utf-8", errors="replace"), ""


def read_claims(kernel: dict, uri: str) -> tuple[str | None, str, list]:
    """The text of the kernel's claims about an entity (their literal values, one per line), or (None, why).
    `kernel` maps a subject IRI to [(claim id, value)], already without the citing agent's own claims."""
    found = kernel.get(uri) or []
    if not found:
        return None, "no claims about it in the kernel", []
    return "\n".join(v for _, v in found), "", [str(i) for i, _ in found]


def verdict(refs: list[dict], store: Path, root: Path, kernel: dict | None = None) -> tuple[str | None, dict]:
    """The verdict on one claim from its quoted references: 'supported' when every quote is found,
    'refuted' when any readable source lacks its quote, None (leave unverified) when a source cannot be
    read here and no quote was refuted. A source is an artifact, a file: URI, or an entity the kernel
    has claims about (D-71: an answer citing the kernel is checked against the claims it cites)."""
    checked, unreadable = [], []
    for r in refs:
        if not r.get("quote"):
            continue
        cited = []
        if r.get("artifact"):
            text, why = read_artifact(store, r["artifact"])
            source = r["artifact"]
        elif (r.get("source_uri") or "").startswith("file:"):
            text, why = read_file(root, r["source_uri"])
            source = r["source_uri"]
        else:
            source = r.get("source_uri")
            text, why, cited = read_claims(kernel or {}, source or "")
        if text is None:
            unreadable.append({"source": source, "why": why})
            continue
        how = match(r["quote"], text)
        checked.append({"source": source, "found": how or "no",
                        "source_sha256": hashlib.sha256(text.encode()).hexdigest(), **({"claims": cited} if cited else {})})
    details = {"references": checked, **({"unreadable": unreadable} if unreadable else {})}
    if any(c["found"] == "no" for c in checked):
        return "refuted", details
    if checked and not unreadable:
        return "supported", details
    return None, details
