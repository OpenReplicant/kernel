"""Is a pinned span still where it was pinned? Pure functions, standard library only; no database, no model.

A claim that rests on a file cites a span: the file's URI (`span.source`), a line locator (`L12` or `L12-14`)
and the text quoted from those lines (`quote`). The span is unchanged when the quote is still in the file's
lines at the locator (exact, or after the quote verifier's normalisation). Otherwise:

  moved    the text is now at exactly one other place (a window as tall as the quote); `moved_to` says where
  changed  the text is not at the locator and not found at exactly one other place
  gone     the file is not under any source root

A locator that is not a line range is not checked here, as in Substrate's store (`stale_file_spans`).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from verifier_quotes.check import match, read_file

METHOD = "urn:kk:module:watcher-spans"


def lines_of(locator: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"L(\d+)(?:-(\d+))?", locator or "")
    if not m:
        return None
    a, b = int(m[1]), int(m[2] or m[1])
    return (a, b) if 1 <= a <= b else None


def read(roots: list[Path], uri: str) -> str | None:
    """The file's text from the first root that has it, or None."""
    for root in roots:
        text, _ = read_file(root, uri)
        if text is not None:
            return text
    return None


def judge(quote: str, locator: str, text: str | None) -> dict | None:
    """None when the span is unchanged or not checkable; else {"why": moved | changed | gone, ...}."""
    span = lines_of(locator)
    if span is None or not quote:
        return None
    if text is None:
        return {"why": "gone"}
    lines = text.split("\n")
    a, b = span
    if match(quote, "\n".join(lines[a - 1:b])):
        return None
    n = quote.count("\n") + 1                # a window as tall as the quote, wherever it now starts
    found = [i + 1 for i in range(len(lines)) if match(quote, "\n".join(lines[i:i + n]))]
    if len(found) == 1:
        at = found[0]
        return {"why": "moved", "moved_to": f"L{at}" if n == 1 else f"L{at}-{at + n - 1}"}
    return {"why": "changed", **({"found_at": [f"L{i}" for i in found[:5]]} if found else {})}


def sha256(quote: str) -> str:
    return hashlib.sha256(quote.encode()).hexdigest()


def verdicts(problems: dict[str, list[dict]], latest: dict[str, str]) -> list[tuple[str, str, dict]]:
    """(claim id, verdict, details) for every claim whose verdict should change.
    problems: every claim checked this pass -> its changed spans (empty when all are as pinned);
    latest: claim id -> this method's latest verdict on it."""
    out = []
    for claim, changed in sorted(problems.items()):
        if changed and latest.get(claim) != "inconclusive":
            out.append((claim, "inconclusive", {"check": "pinned span", "changed": changed}))
        elif not changed and latest.get(claim) == "inconclusive":
            out.append((claim, "supported", {"check": "pinned span", "changed": []}))
    return out
