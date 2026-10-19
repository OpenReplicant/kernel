"""What truth maintenance records (D-89, M6-T12). Pure functions; no database.

A claim is *undermined* when something it was derived from (directly, or through other derived claims) has
been withdrawn: superseded, refuted, retracted or redacted. For each current claim:

  undermined, and its latest verdict from this method is not inconclusive -> inconclusive, naming the
                                                                             withdrawn premises
  not undermined, and its latest verdict from this method is inconclusive -> supported: its premises are
                                                                             current again
  otherwise                                                               -> nothing

Nothing is deleted and no claim is written: a verdict is a verification, and the belief model caps the
belief of a claim whose latest verdict here is inconclusive.
"""
from __future__ import annotations

METHOD = "urn:kk:module:truth-maintenance"


def verdicts(undermined: dict[str, list[str]], latest: dict[str, str]) -> list[tuple[str, str, dict]]:
    """(claim id, verdict, details) for every claim whose verdict should change.
    undermined: current claim id -> the withdrawn claims behind it; latest: claim id -> this method's latest
    verdict on it (only claims it has judged, and still current)."""
    out = []
    for claim, gone in sorted(undermined.items()):
        if latest.get(claim) != "inconclusive":
            out.append((claim, "inconclusive", {"check": "premises current", "withdrawn": sorted(gone)[:50]}))
    for claim, verdict in sorted(latest.items()):
        if verdict == "inconclusive" and claim not in undermined:
            out.append((claim, "supported", {"check": "premises current", "withdrawn": []}))
    return out
