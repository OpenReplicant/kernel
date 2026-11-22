"""One step of a live conversation for scripts/e2e_map.mjs (D-116), the way scripts/e2e_interview.py runs one: the
interaction encoder records the chat so far, then the agent submits what the person's answer says (the scripted
interview of modules/skill-interview/tests/fixtures, on an organisation and process of its own so the map grows).

  python scripts/e2e_map_feed.py SLUG answer N     the Nth answer (0..4): the interaction encoder, then the agent's claims, applied
  python scripts/e2e_map_feed.py SLUG proposal     the agent proposes a post and who it reports to, in the review lane

Env as e2e_interview.py: DATABASE_URL, AGENT_DATABASE_URL, KK_ARTIFACTS; the knowledge-kernel package installed."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "modules/skill-interview/tests"))
from e2e_interview import module, settled  # noqa: E402
from scripted import load  # noqa: E402


def main(slug: str, what: str, n: int = 0) -> int:
    messages, script, answers = load(slug)
    own = lambda text: text.replace("urn:kk:org:acme", f"urn:kk:org:{slug}").replace(  # noqa: E731
        "urn:kk:person:acme:", f"urn:kk:person:{slug}:").replace("urn:kk:process:purchasing", f"urn:kk:process:{slug}")
    agent = psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True)
    me = agent.execute("select kernel.my_agent()").fetchone()[0]
    if what == "answer":
        a = answers[n]
        chat = Path(tempfile.gettempdir()) / f"{slug}.jsonl"
        chat.write_text("".join(json.dumps(m) + "\n" for m in messages if int(m["id"]) <= int(a["id"])))
        module(["interaction_encoder", str(chat), "--source", slug], "kkm_encoder_interaction")
        settled("kkm_encoder_interaction")
        ops, key, lane = json.loads(own(json.dumps(a["ops"]))), f"map-feed:{slug}|{a['message']}", "auto"
    else:
        message = answers[-1]["message"]
        post = f"urn:kk:org:{slug}:post:receiving-clerk"
        ref = [{"source_uri": message}]
        ops = [{"op": "add_claim", "subject": post, "predicate": "rdf:type", "object": {"entity": "http://www.w3.org/ns/org#Post"},
                "evidence_type": "testimony", "references": ref},
               {"op": "add_claim", "subject": post, "predicate": "rdfs:label", "object": {"value": {"@value": "Receiving clerk"}},
                "evidence_type": "testimony", "references": ref},
               {"op": "add_claim", "subject": post, "predicate": "http://www.w3.org/ns/org#reportsTo",
                "object": {"entity": f"urn:kk:org:{slug}:post:buyer"}, "evidence_type": "testimony", "references": ref}]
        key, lane = f"map-feed:{slug}|proposal", "review"
    run = agent.execute("select api.start_run(null, null, '[]'::jsonb, %s)", (Jsonb({"purpose": "live map test"}),)).fetchone()[0]
    cs = agent.execute("select api.submit_change_set(%s)", (Jsonb({
        "idempotency_key": key, "attributed_to": me, "generated_by": str(run), "target_scope": "urn:kk:scope:default",
        "lane": lane, "ops": ops}),)).fetchone()[0]
    for _ in range(240):
        status = agent.execute("select status from api.change_sets where id = %s", (cs["id"],)).fetchone()[0]
        if status not in ("submitted", "valid"):
            break
        time.sleep(0.25)
    print(json.dumps({"id": cs["id"], "status": status}))
    return 0 if status == ("awaiting_review" if lane == "review" else "applied") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0))
