"""A stored log -> its change sets (M3-T4). No database: unit-testable without psycopg.

The log is hashed and copied into the artifact store in blocks; XES (also .xes.gz) is encoded as a
stream of traces; the claims are split into change sets of at most `batch` claims, keyed by the log's
hash and the batch number, the first carrying the artifact. Earlier claims (another version of the
log) are superseded in the set that holds their replacement, the rest retracted in the last set.
"""
from __future__ import annotations

import gzip
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

from kk.changes import same, supersede

from .encode import encode, encode_traces, read_csv, traces_xes

AGENT = "urn:kk:agent:encoder-eventlog"
SCOPE = "urn:kk:scope:default"


def store(log: Path, artifacts: Path) -> tuple[str, int]:
    """Hash the log and copy it into the content-addressed store, in blocks: (sha256, size)."""
    h, size = hashlib.sha256(), 0
    with open(log, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
            size += len(block)
    sha = h.hexdigest()
    artifacts.mkdir(parents=True, exist_ok=True)
    if not (artifacts / sha).exists():
        shutil.copyfile(log, artifacts / f"{sha}.part")
        (artifacts / f"{sha}.part").rename(artifacts / sha)
    return sha, size


def claims(log: Path, a, sha: str) -> tuple[list[dict], str, int]:
    """The add_claim ops, the log's media type and its number of events."""
    name = log.name.lower()
    if name.endswith((".xes", ".xes.gz")):
        n = [0]

        def counted(traces):
            for case, events in traces:
                n[0] += len(events)
                yield case, events
        with (gzip.open if name.endswith(".gz") else open)(log, "rb") as f:
            ops = encode_traces(counted(traces_xes(f)), a.process, a.window, sha)
        return ops, "application/gzip" if name.endswith(".gz") else "application/xml", n[0]
    events = read_csv(log.read_bytes(), a.case, a.activity, a.timestamp, a.resource or None)
    return encode(events, a.process, a.window, sha), "text/csv", len(events)


def version(log: Path, a, sha: str) -> str:
    """The key prefix of this version of the log (its change sets add the batch number)."""
    return f"{prefix(log, a)}{a.window}|{sha[:16]}|"


def change_sets(log: Path, a, artifacts: Path, sha: str, size: int, prior: list[dict]) -> tuple[list[dict], int]:
    """The change sets for one stored log: at most --batch claims each, keyed by the log's hash and the
    batch; each supersedes the earlier claims its own claims replace; the last retracts the rest."""
    ops, media, n_events = claims(log, a, sha)
    sets = [{"idempotency_key": f"{version(log, a, sha)}{i // a.batch}", "attributed_to": AGENT, "target_scope": SCOPE,
             "ops": ops[i:i + a.batch]} for i in range(0, len(ops), a.batch)]
    sets[0]["ops"].insert(0, {"op": "add_artifact", "sha256": sha, "media_type": media, "byte_size": size,
                              "location": f"file://{artifacts / sha}", "source_uri": f"file:{log.name}",
                              "retrieved_at": datetime.now(timezone.utc).isoformat()})
    by_same = {same(c["subject"], c["predicate"], c["qualifiers"], c["valid_from"]): c for c in prior}
    left = dict(by_same)
    for cs in sets:
        mine = [left.pop(k) for k in {same(op["subject"], op["predicate"], op.get("qualifiers"), op.get("valid_from"))
                                      for op in cs["ops"] if op["op"] == "add_claim"} if k in left]
        if mine:
            supersede(cs, mine)
    if left:
        supersede(sets[-1], list(left.values()))
    return sets, n_events


def prefix(log: Path, a) -> str:
    return f"eventlog:{a.process}|{log.name}|"
