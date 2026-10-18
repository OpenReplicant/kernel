"""Interaction encoder: a transcript of a conversation or an agent session into claims (M5-T3, D-81).
A plain command (D-41).

  python -m interaction_encoder [--source NAME] [--format auto|chat|claude-code] [--person NAME]
                                [--agent NAME] [--scope IRI] [--batch N] [--artifacts DIR] [--no-filter] FILE

1. The ingest filter (fail closed): an event on a line with a finding is left out and reported, never
   stored; a finding without a line holds back the whole file.
2. The events (encode.py) are rendered as a plain-text transcript, stored as the artifact every claim
   cites with its quote; the source file itself is not stored.
3. Change sets of at most --batch claims (default 5000), keyed by the source name, the file name and
   the transcript's hash: the same transcript again is a no-op; a grown or changed one supersedes the
   claims of the earlier version.

Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_encoder_interaction), KK_INGEST_FILTER, KK_ARTIFACTS (default ~/.local/share/knowledge-kernel/artifacts).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from kk.lib import connect, ingest_filter, run, submit

from .encode import change_sets, encode, read_chat, read_claude_code, render, sniff

MODULE = "urn:kk:module:encoder-interaction"
AGENT = "urn:kk:agent:encoder-interaction"
# This encoder's current claims from other versions of the same transcript (same source and file name).
PRIOR = """select c.id, c.subject, c.predicate, c.qualifiers, c.valid_from, c.status
           from api.claims c join api.change_sets s on s.id = c.change_set
           where c.attributed_to = %s and c.status <> 'deprecated'
             and starts_with(s.idempotency_key, %s) and not starts_with(s.idempotency_key, %s)"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Encode a conversation or agent-session transcript into claims.")
    ap.add_argument("file")
    ap.add_argument("--source", help="a short name for where the transcript comes from (default: the file's stem)")
    ap.add_argument("--format", choices=("auto", "chat", "claude-code"), default="auto")
    ap.add_argument("--person", default="person", help="claude-code: the person's name (default person)")
    ap.add_argument("--agent", default="claude-code", help="claude-code: the agent's name (default claude-code)")
    ap.add_argument("--scope", default="urn:kk:scope:default")
    ap.add_argument("--batch", type=int, default=5000)
    ap.add_argument("--artifacts", default=os.environ.get("KK_ARTIFACTS")
                    or str(Path.home() / ".local/share/knowledge-kernel/artifacts"))
    ap.add_argument("--no-filter", action="store_true", help="skip the ingest filter (secrets may be stored)")
    a = ap.parse_args(argv)
    path = Path(a.file).resolve()
    source = a.source or path.stem
    lines = path.read_text(encoding="utf-8").splitlines()
    blocked: set[int] = set()
    skipped: list[str] = []
    if not a.no_filter:
        found = ingest_filter(path.parent, [path.name])
        if any(b.get("line") is None for b in found["blocked"]):
            print(json.dumps({"blocked": found["blocked"]}))
            return 1
        blocked = {b["line"] for b in found["blocked"]}
        skipped = [f"line {b['line']}: {b['rule']}" for b in found["blocked"]]
    fmt = a.format if a.format != "auto" else sniff(next(line for line in lines if line.strip()))
    events = read_chat(lines) if fmt == "chat" else read_claude_code(lines, a.person, a.agent)
    events = [e for e in events if e.line not in blocked]
    text, _ = render(events)
    data = text.encode()
    sha = hashlib.sha256(data).hexdigest()
    store = Path(a.artifacts)
    store.mkdir(parents=True, exist_ok=True)
    if not (store / sha).exists():
        (store / f"{sha}.part").write_bytes(data)
        (store / f"{sha}.part").rename(store / sha)
    artifact = {"op": "add_artifact", "sha256": sha, "media_type": "text/plain", "byte_size": len(data),
                "location": f"file://{store / sha}", "source_uri": f"file:{path.name}",
                "retrieved_at": datetime.now(timezone.utc).isoformat()}
    prefix = f"interaction:{source}|{path.name}|"
    with connect(os.environ.get("KK_ROLE", "kkm_encoder_interaction")) as conn, conn.cursor() as cur:
        with run(cur, MODULE, {"file": path.name, "source": source, "format": fmt}) as (run_id, outputs):
            ops = encode(events, source, sha)
            cur.execute(PRIOR, (AGENT, prefix, f"{prefix}{sha[:16]}|"))
            cols = [d.name for d in cur.description]
            prior = [dict(zip(cols, r)) for r in cur.fetchall()]
            sets = change_sets(ops, artifact, f"{prefix}{sha[:16]}|", a.batch, prior, AGENT, a.scope)
            ids = [str(submit(cur, cs, run_id)["id"]) for cs in sets]
            outputs.update(events=len(events), claims=len(ops), change_sets=len(ids))
    print(json.dumps({"change_set": ids[-1], "change_sets": ids, "format": fmt, "events": len(events),
                      "messages": sum(e.kind == "message" for e in events), "actions": sum(e.kind == "action" for e in events),
                      "claims": len(ops), "superseded": len(prior), "artifact": sha, "skipped": skipped}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
