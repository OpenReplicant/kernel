"""Prometheus query results -> one change set of observations for one window (D-52). Pure functions.

For each query and each series it returns (labelled `job`, the compose service):

  urn:kk:node:<job>  sosa:hasSimpleResult <value>   {sosa:observedProperty: <property>}
                     valid from the window's start to its end, evidence observed,
                     referenced by the exact query URL (Prometheus HTTP API, evaluated at the window end)

The subject is the compose service's node in the self-model (self-encoder, kkt:NodeTemplate).

A query with an `edge` (D-100) records calls instead: each series with a value above zero gives

  urn:kk:node:<from>  kkt:connectsTo  urn:kk:node:<to>    valid for the window, evidence observed

where `from` and `to` name the series' labels (an OpenTelemetry service graph's `client` and `server`), or, after
`=`, a fixed node (`=front-door` for the spans one service makes per route).
"""
from __future__ import annotations

import math
import urllib.parse
from datetime import datetime, timezone

SOSA = "http://www.w3.org/ns/sosa/"
XSD = "http://www.w3.org/2001/XMLSchema#"
CONNECTS_TO = "https://w3id.org/knowledge-kernel/tosca#connectsTo"


def query_url(base: str, query: str, at: float) -> str:
    return f"{base.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": query, "time": f"{at:.0f}"})


def window(now: float, seconds: int) -> tuple[float, float]:
    """The last complete window before `now`, aligned to multiples of `seconds`."""
    end = math.floor(now / seconds) * seconds
    return end - seconds, end


def end_of(series: dict, spec: str) -> str | None:
    """An edge end: the named label's value, or after '=' a fixed name."""
    return spec[1:] if spec.startswith("=") else series.get("metric", {}).get(spec)


def ops(results: list[tuple[str | dict, str, dict]], start: float, end: float) -> list[dict]:
    """results: (property, or an edge {from, to}, query URL, Prometheus response JSON) per query."""
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
    out = []
    for prop, url, resp in results:
        if resp.get("status") != "success" or resp.get("data", {}).get("resultType") != "vector":
            continue
        if isinstance(prop, dict):
            seen = set()
            for series in resp["data"]["result"]:
                a, b = end_of(series, prop["from"]), end_of(series, prop["to"])
                try:
                    value = float(series.get("value", [None, None])[1])
                except (TypeError, ValueError):
                    continue
                if not a or not b or a == b or (a, b) in seen or math.isnan(value) or value <= 0:
                    continue
                seen.add((a, b))
                out.append({"op": "add_claim", "subject": f"urn:kk:node:{a}", "predicate": CONNECTS_TO,
                            "object": {"entity": f"urn:kk:node:{b}"}, "valid_from": iso(start), "valid_to": iso(end),
                            "evidence_type": "observed", "references": [{"source_uri": url}]})
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
