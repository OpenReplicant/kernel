"""encoder-morphogen checks (D-99): Morphogen's verdicts become claims, nothing else.

  python modules/encoder-morphogen/tests/test_encode.py
"""
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from encoder_morphogen.encode import (DQV, PROV, RDFS, ChainBroken, canonical, claims, read_log)  # noqa: E402

checks = []


def check(name, ok):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name)


def write_log(path: Path, records: list[dict], tamper: int | None = None) -> None:
    """A log the way Morphogen writes one (morphogen/evidence.py): each record hashed with the one before."""
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE evidence (seq INTEGER PRIMARY KEY, hash TEXT NOT NULL, prev TEXT NOT NULL, raw TEXT NOT NULL)")
    prev = "0" * 64
    for i, r in enumerate(records, start=1):
        rec = {**r, "seq": i, "at": f"2026-11-07T10:0{i}:00Z", "prev": prev}
        rec["hash"] = hashlib.sha256(canonical(rec).encode()).hexdigest()
        raw = rec if tamper != i else {**rec, "claim": rec["claim"] + " (edited)"}
        c.execute("INSERT INTO evidence VALUES (?, ?, ?, ?)", (i, rec["hash"], prev, canonical(raw)))
        prev = rec["hash"]
    c.commit()
    c.close()


base, cand = "1" * 40, "2" * 40
passed = {"claim": f"bifrost-config: {cand[:12]} is better than {base[:12]}", "kind": "verdict", "verdict": "pass",
          "subject": {"system": "bifrost-config", "path": "deploy"}, "cell": "model-gateway",
          "refs": {"base": base, "candidate": cand},
          "outcome": {"metrics": {"p50_ms": {"base": 120.0, "candidate": 102.0, "gain": 0.15, "ci95": [0.1, 0.2],
                                             "sign_test_p": 0.001, "ok": True, "primary": True, "runs": 40}}},
          "gate": {"decision": "review", "lifecycle": "experimental", "owner": "group:operator"},
          "trust": "measured", "by": "judge"}
failed = {**passed, "claim": f"resolver: {cand[:12]} is not better than {base[:12]}", "verdict": "fail",
          "subject": {"system": "resolver"}, "cell": None, "trust": "simulated",
          "outcome": {"metrics": {"f1": {"base": 0.91, "candidate": 0.78, "gain": -0.1429, "sign_test_p": 0.02,
                                         "ok": False, "runs": 12}}},
          "gate": {"decision": "none"}}
note = {"claim": "the operator says the base is slow", "trust": "asserted", "by": "operator"}

with tempfile.TemporaryDirectory() as d:
    log = Path(d) / "evidence.db"
    write_log(log, [passed, note, failed])
    recs = read_log(log)
    check("the log is read in order and its chain verified", [r["seq"] for r in recs] == [1, 2, 3])
    verdicts = [r for r in recs if r.get("kind") == "verdict" and r.get("by") == "judge"]
    check("only the judge's verdicts are recorded (the operator's note is not)", len(verdicts) == 2)

    sha = "a" * 64
    ops = claims(verdicts[0], "substrate", sha)
    by = {}
    for op in ops:
        by.setdefault(op["predicate"], []).append(op)
    s = "urn:kk:verdict:substrate:1"
    check("one subject per verdict, labelled with the judge's claim",
          all(op["subject"] == s for op in ops) and by[RDFS + "label"][0]["object"]["value"]["@value"] == passed["claim"])
    check("its outcome and who may act on it",
          {o["object"]["value"]["@value"] for o in by["http://purl.org/dc/terms/type"]} == {"verdict: pass", "gate: review"})
    check("the part judged, as kk-tosca names it", by["http://purl.org/dc/terms/subject"][0]["object"] == {"entity": "urn:kk:node:model-gateway"})
    used = sorted(o["object"]["entity"] for o in by[PROV + "used"])
    check("the base and candidate commits it used (PROV)", used == [f"urn:kk:commit:{base}", f"urn:kk:commit:{cand}"])
    m = {(o["qualifiers"][DQV + "isMeasurementOf"], o["qualifiers"][DQV + "computedOn"]): o["object"]["value"]["@value"]
         for o in by[DQV + "value"]}
    check("gain, p and both medians as DQV measurements, each on the right commit",
          m == {("urn:kk:metric:morphogen:p50_ms:gain", f"urn:kk:commit:{cand}"): 0.15,
                ("urn:kk:metric:morphogen:p50_ms:sign-test-p", f"urn:kk:commit:{cand}"): 0.001,
                ("urn:kk:metric:morphogen:p50_ms", f"urn:kk:commit:{base}"): 120.0,
                ("urn:kk:metric:morphogen:p50_ms", f"urn:kk:commit:{cand}"): 102.0})
    check("measured verdicts are observed claims; every claim cites the stored record by JSON pointer",
          all(o["evidence_type"] in ("observed", "deterministic") for o in ops)
          and all(o["references"][0]["artifact"] == sha and o["references"][0]["span"]["value"].startswith("/") for o in ops)
          and by[DQV + "value"][0]["evidence_type"] == "observed")
    sim = claims(verdicts[1], "substrate", sha)
    check("a verdict on a model of the system (simulated) is inferred, not observed",
          {o["evidence_type"] for o in sim if o["predicate"] == DQV + "value"} == {"inferred"})
    check("a verdict without a cell names the system it judged",
          [o["object"] for o in sim if o["predicate"] == "http://purl.org/dc/terms/subject"] == [{"entity": "urn:kk:node:resolver"}])
    check("the same verdict gives the same claims (re-running changes nothing)", claims(verdicts[0], "substrate", sha) == ops)

    bad = Path(d) / "tampered.db"
    write_log(bad, [passed, failed], tamper=2)
    try:
        read_log(bad)
        refused = False
    except ChainBroken as e:
        refused = e.seq == 2
    check("a log whose hash chain is broken is refused, naming the first bad record", refused)

print(f"encoder-morphogen checks passed ({sum(checks)}/{len(checks)})" if all(checks) else "encoder-morphogen checks FAILED")
sys.exit(0 if all(checks) else 1)
