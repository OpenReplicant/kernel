"""Morphogen's verdicts into DQV measurements and PROV (D-99). Pure functions, standard library only.

Morphogen's evidence log is SQLite (`<git common dir>/morphogen/evidence.db`, table evidence(seq, hash, prev,
raw)): each record's hash covers its canonical JSON without the hash, and names the one before it. The chain is
checked here, independently of Morphogen, before anything is read. Each verdict the judge wrote becomes, about
`urn:kk:verdict:<project>:<seq>`:

  rdfs:label       the judge's claim ("cell: <candidate> is better than <base>")
  dcterms:type     "verdict: pass|fail", and "gate: merge|review|none" (who may act on it)
  dcterms:subject  the part judged, urn:kk:node:<cell or system> (kk-tosca's node names)
  prov:used        urn:kk:commit:<base> and urn:kk:commit:<candidate>
  dqv:value        per metric: the gain and the sign test's p on the candidate commit, and each commit's median
                   {dqv:isMeasurementOf urn:kk:metric:morphogen:<metric>[:gain|:sign-test-p], dqv:computedOn the commit}

Every claim references the stored record (an artifact) and its field by JSON pointer (RFC 6901). Measurements on
the real system (trust `measured`) are observed; on a model of it (`simulated`), inferred.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

DQV = "http://www.w3.org/ns/dqv#"
PROV = "http://www.w3.org/ns/prov#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
DCT = "http://purl.org/dc/terms/"
XSD = "http://www.w3.org/2001/XMLSchema#"
GENESIS = "0" * 64
EVIDENCE = {"measured": "observed", "simulated": "inferred"}


class ChainBroken(Exception):
    def __init__(self, seq: int):
        super().__init__(f"Morphogen's evidence log is broken at record {seq}: its hash or link does not match")
        self.seq = seq


def canonical(obj) -> str:
    """Morphogen's canonical JSON (morphogen/evidence.py), which its hashes cover."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9@._-]+", "-", str(text)).strip("-")


def read_log(path: Path) -> list[dict]:
    """Every record, oldest first, after checking the whole chain. Read from a copy, so a read-only mount and a
    writer's WAL file do not get in each other's way."""
    with tempfile.TemporaryDirectory() as d:
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(path) + suffix)
            if p.exists():
                shutil.copy(p, Path(d) / ("evidence.db" + suffix))
        c = sqlite3.connect(Path(d) / "evidence.db")
        try:
            rows = c.execute("SELECT seq, raw FROM evidence ORDER BY seq").fetchall()
        finally:
            c.close()
    out, prev = [], GENESIS
    for i, (seq, raw) in enumerate(rows, start=1):
        rec = json.loads(raw)
        body = {k: v for k, v in rec.items() if k != "hash"}
        if seq != i or rec.get("seq") != i or rec.get("prev") != prev or \
                hashlib.sha256(canonical(body).encode()).hexdigest() != rec.get("hash"):
            raise ChainBroken(i)
        prev = rec["hash"]
        out.append(rec)
    return out


def pointer(*path) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in path)


def number(v) -> dict:
    return {"@value": v, "@type": XSD + ("integer" if isinstance(v, int) else "decimal")}


def claims(rec: dict, project: str, sha256: str) -> list[dict]:
    """The add_claim ops for one verdict record stored as artifact `sha256` (the artifact op is the caller's)."""
    s = f"urn:kk:verdict:{slug(project)}:{rec['seq']}"
    measured = EVIDENCE.get(rec.get("trust"), "inferred")
    refs = rec.get("refs") or {}
    commit = {k: f"urn:kk:commit:{v}" for k, v in refs.items() if v}
    part = rec.get("cell") or (rec.get("subject") or {}).get("system")

    def ref(*path):
        return [{"artifact": sha256, "span": {"type": "FragmentSelector", "value": pointer(*path),
                                              "conformsTo": "https://www.rfc-editor.org/rfc/rfc6901"}}]

    def claim(predicate, obj, evidence, path, qualifiers=None):
        op = {"op": "add_claim", "subject": s, "predicate": predicate, "object": obj,
              "evidence_type": evidence, "references": ref(*path)}
        if qualifiers:
            op["qualifiers"] = qualifiers
        return op

    ops = [claim(RDFS + "label", {"value": {"@value": rec["claim"]}}, "deterministic", ("claim",)),
           claim(DCT + "type", {"value": {"@value": f"verdict: {rec.get('verdict')}"}}, "deterministic", ("verdict",))]
    gate = (rec.get("gate") or {}).get("decision")
    if gate:
        ops.append(claim(DCT + "type", {"value": {"@value": f"gate: {gate}"}}, "deterministic", ("gate", "decision")))
    if part:
        ops.append(claim(DCT + "subject", {"entity": f"urn:kk:node:{slug(part)}"}, "deterministic",
                         ("cell",) if rec.get("cell") else ("subject", "system")))
    for role in ("base", "candidate"):
        if role in commit:
            ops.append(claim(PROV + "used", {"entity": commit[role]}, "deterministic", ("refs", role)))
    on = commit.get("candidate")
    for metric, c in sorted(((rec.get("outcome") or {}).get("metrics") or {}).items()):
        if not isinstance(c, dict):
            continue
        m = f"urn:kk:metric:morphogen:{slug(metric)}"
        for field, suffix, where in (("gain", ":gain", on), ("sign_test_p", ":sign-test-p", on),
                                     ("base", "", commit.get("base")), ("candidate", "", on)):
            v = c.get(field)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and where:
                ops.append(claim(DQV + "value", {"value": number(v)}, measured, ("outcome", "metrics", metric, field),
                                 {DQV + "isMeasurementOf": m + suffix, DQV + "computedOn": where}))
    return ops
