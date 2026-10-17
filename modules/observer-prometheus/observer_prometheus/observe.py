"""Prometheus query results -> one change set of observations for one window (D-52). Pure functions.

For each query and each series it returns (labelled `job`, the compose service):

  urn:kk:node:<job>  sosa:hasSimpleResult <value>   {sosa:observedProperty: <property>}
                     valid from the window's start to its end, evidence observed,
                     referenced by the exact query URL (Prometheus HTTP API, evaluated at the window end)

The subject is the compose service's node in the self-model (self-encoder, kkt:NodeTemplate).
"""
from __future__ import annotations

import math
import urllib.parse
from datetime import datetime, timezone

SOSA = "http://www.w3.org/ns/sosa/"
XSD = "http://www.w3.org/2001/XMLSchema#"


def query_url(base: str, query: str, at: float) -> str:
    return f"{base.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": query, "time": f"{at:.0f}"})


def window(now: float, seconds: int) -> tuple[float, float]:
    """The last complete window before `now`, aligned to multiples of `seconds`."""
    end = math.floor(now / seconds) * seconds
    return end - seconds, end


def ops(results: list[tuple[str, str, dict]], start: float, end: float) -> list[dict]:
    """results: (property, query URL, Prometheus response JSON) per query."""
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
    out = []
    for prop, url, resp in results:
        if resp.get("status") != "success" or resp.get("data", {}).get("resultType") != "vector":
            continue
        for series in resp["data"]["result"]:
            job, raw = series.get("metric", {}).get("job"), series.get("value", [None, None])[1]
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if not job or math.isnan(value) or math.isinf(value):
                continue
            out.append({"op": "add_claim", "subject": f"urn:kk:node:{job}", "predicate": SOSA + "hasSimpleResult",
                        "object": {"value": {"@value": round(value, 4), "@type": XSD + "decimal"}},
                        "qualifiers": {SOSA + "observedProperty": prop},
                        "valid_from": iso(start), "valid_to": iso(end), "evidence_type": "observed",
                        "references": [{"source_uri": url}]})
    return out
