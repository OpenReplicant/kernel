"""Self-encoder: scan a repository into the kernel. A plain command; needs no runner (D-41).

  python -m self_encoder scan [--repo DIR] [--name NAME] [--artifacts DIR] [--no-filter]

1. List the files the self-model is built from (encode.list_files).
2. Run the ingest filter (KK_INGEST_FILTER, default `python -m ingest_filter`); files with secrets are
   skipped and reported, never stored. Without a filter the scan refuses unless --no-filter is given.
3. Encode each clean file into one change set (artifact bytes go to --artifacts).
4. Supersede on re-ingest: a changed file's change set also deprecates this encoder's claims from the
   file's earlier versions (superseded by the new claim with the same subject, predicate and object,
   or the same subject and predicate; otherwise retracted). A file that disappeared gets a change set
   retracting its claims. An unchanged file resubmits the same idempotency key, which is a no-op.
5. Submit everything under one run of the self-encoder's role (its info names the repository and the
   commit it read, so the session hook can tell when the checkout moved on), then print a summary.

Env: DATABASE_URL (a login that may SET ROLE to KK_ROLE, or the module's own login), KK_ROLE (default
kkm_self_encoder), KK_INGEST_FILTER, KK_ARTIFACTS (default ~/.local/share/knowledge-kernel/artifacts).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

from kk.lib import connect, ingest_filter, run, submit

from .encode import AGENT, SCOPE, encode_file, key_prefix, list_files
from .repo import REQUIRES, SUGGESTS, commit, repo_name

MODULE = "urn:kk:module:self-encoder"
# Predicates with many values per subject: a value that is gone is retracted, never "superseded" by a
# sibling (a removed dependency is not replaced by another one).
MANY = {REQUIRES, SUGGESTS}
PRIOR = """select c.id, c.subject, c.predicate, c.object_entity, c.object_value, c.status, s.idempotency_key
           from api.claims c join api.change_sets s on s.id = c.change_set
           where c.attributed_to = %s and c.status <> 'deprecated' and starts_with(s.idempotency_key, %s)"""


def path_of(key: str, prefix: str) -> str:
    """'<prefix><path>:<sha16>' or '<prefix><path>:removed:<hash>' -> <path>."""
    rest = key[len(prefix):]
    return rest.split(":removed:")[0] if ":removed:" in rest else rest.rsplit(":", 1)[0]


def canonical(entity, value) -> str:
    return json.dumps({"entity": entity} if entity else {"value": value}, sort_keys=True)


def supersede(cs: dict, prior: list[dict], expand) -> None:
    """Append set_status ops deprecating `prior` (claims from earlier versions of this file)."""
    by_spo, by_sp = {}, {}
    for i, op in enumerate(cs["ops"]):
        if op["op"] != "add_claim":
            continue
        op["ref"] = f"c{i}"
        o = op["object"]
        obj = canonical(expand(o["entity"]) if "entity" in o else None,
                        None if "entity" in o else {**o["value"], **({"@type": expand(o["value"]["@type"])}
                                                                     if "@type" in o["value"] else {})})
        s, p = expand(op["subject"]), expand(op["predicate"])
        by_spo.setdefault((s, p, obj), op["ref"])
        by_sp.setdefault((s, p), op["ref"])
    for c in prior:
        obj = canonical(c["object_entity"], c["object_value"])
        new = by_spo.get((c["subject"], c["predicate"], obj)) or (
            None if c["predicate"] in MANY else by_sp.get((c["subject"], c["predicate"])))
        cs["ops"].append({"op": "set_status", "claim": str(c["id"]), "status": "deprecated",
                          "expect_status": c["status"], **({"reason": "superseded", "replaced_by": new}
                                                        if new else {"reason": "retracted"})})


def scan(conn, repo: Path, artifacts: Path, use_filter: bool, name: str | None = None,
         at: str | None = None) -> dict:
    prefixes = dict(conn.execute("select prefix, namespace from kernel.prefix").fetchall())

    def expand(term: str) -> str:
        prefix, sep, local = term.partition(":")
        return prefixes[prefix] + local if sep and prefix in prefixes and not local.startswith("//") else term

    files = list_files(repo)
    filtered = ingest_filter(repo, files) if use_filter else {"paths": files, "blocked": []}
    prior: dict[str, list] = {}
    prefix = key_prefix(repo, name)
    cur = conn.execute(PRIOR, (AGENT, prefix))
    cols = [d.name for d in cur.description]
    for row in cur.fetchall():
        r = dict(zip(cols, row))
        prior.setdefault(path_of(r["idempotency_key"], prefix), []).append(r)

    statuses: Counter = Counter()
    info = {"repo": str(repo), "repo_name": repo_name(repo, name), "commit": at or commit(repo),
            "filtered": use_filter}
    with conn.cursor() as cur, run(cur, MODULE, info) as (run_id, outputs):
        for rel in filtered["paths"]:
            cs = encode_file(repo, rel, artifacts, name)
            old = [c for c in prior.get(rel, []) if c["idempotency_key"] != cs["idempotency_key"]]
            if old:
                supersede(cs, old, expand)
            statuses[submit(cur, cs, run_id)["status"]] += 1
        for rel in sorted(set(prior) - set(files)):
            gone = sorted(prior[rel], key=lambda c: str(c["id"]))
            digest = hashlib.sha256(" ".join(str(c["id"]) for c in gone).encode()).hexdigest()[:16]
            cs = {"idempotency_key": f"{prefix}{rel}:removed:{digest}", "attributed_to": AGENT, "target_scope": SCOPE,
                  "ops": [{"op": "set_status", "claim": str(c["id"]), "status": "deprecated", "reason": "retracted",
                           "expect_status": c["status"]} for c in gone]}
            statuses[submit(cur, cs, run_id)["status"]] += 1
        outputs.update(files=len(filtered["paths"]), blocked=len(filtered["blocked"]))
    return {"run": run_id, "repo": info["repo_name"], "commit": info["commit"], "files": len(files), "encoded": len(filtered["paths"]),
            "blocked": filtered["blocked"], "change_sets": dict(statuses)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scan a repository into the kernel (self-model).")
    ap.add_argument("command", choices=["scan"])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--name", help="the repository's name (default: its folder's)")
    ap.add_argument("--commit", help="the commit being read (default: git rev-parse HEAD in --repo)")
    ap.add_argument("--artifacts", default=os.environ.get("KK_ARTIFACTS")
                    or str(Path.home() / ".local/share/knowledge-kernel/artifacts"))
    ap.add_argument("--no-filter", action="store_true", help="skip the ingest filter (secrets may be stored)")
    a = ap.parse_args(argv)
    with connect(os.environ.get("KK_ROLE", "kkm_self_encoder")) as conn:
        print(json.dumps(scan(conn, Path(a.repo).resolve(), Path(a.artifacts), not a.no_filter, a.name, a.commit)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
