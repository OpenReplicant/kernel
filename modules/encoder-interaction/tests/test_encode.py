"""M5-T3 unit checks (D-81): a chat interview and a Claude Code session give the expected messages and
actions; every claim cites the rendered transcript with a quote that occurs in it; the same input gives
the same claims; a changed transcript supersedes the earlier claims."""
import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT), str(HERE.parent)]
from interaction_encoder.encode import (AS, PROV, change_sets, encode, read_chat, read_claude_code,  # noqa: E402
                                        render, sniff)

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


def claims(events, source):
    text, _ = render(events)
    sha = hashlib.sha256(text.encode()).hexdigest()
    return text, sha, encode(events, source, sha)


def values(ops, subject, predicate):
    return [op["object"].get("entity") or op["object"]["value"]["@value"] for op in ops
            if op["subject"] == subject and op["predicate"] == predicate]


chat = (HERE / "fixtures/interview.jsonl").read_text().splitlines()
check(sniff(chat[0]) == "chat", "a chat file is recognised")
events = read_chat(chat)
check([e.kind for e in events] == ["message"] * 4 + ["action"], "four messages and an action")
text, sha, ops = claims(events, "acme")
m2 = "urn:kk:message:acme:acme-intake:2"
check(values(ops, m2, AS + "attributedTo") == ["urn:kk:actor:acme:dana-reyes"]
      and values(ops, m2, AS + "inReplyTo") == ["urn:kk:message:acme:acme-intake:1"]
      and values(ops, m2, AS + "context") == ["urn:kk:conversation:acme:acme-intake"],
      "a message: its author, the message it answers, its conversation")
check("report to the finance director" in values(ops, m2, AS + "content")[0], "a message's text is its as:content")
check(values(ops, "urn:kk:actor:acme:dana-reyes", "http://www.w3.org/1999/02/22-rdf-syntax-ns#type") == [AS + "Person"]
      and values(ops, "urn:kk:actor:acme:interviewer", "http://www.w3.org/1999/02/22-rdf-syntax-ns#type") == [AS + "Application"],
      "people and agents are typed apart")
act = "urn:kk:action:acme:acme-intake:5"
check(values(ops, act, PROV + "used") == ["file:policies/purchasing.md"]
      and values(ops, act, AS + "instrument") == ["urn:kk:tool:read_file"], "an action: the tool and what it used")
refs = [r for op in ops for r in op["references"]]
check(all(r["artifact"] == sha for r in refs) and all(r["quote"] in text for r in refs if "quote" in r),
      "every claim cites the rendered transcript, and every quote occurs in it")
check(all(op["evidence_type"] == "deterministic" for op in ops), "parsed, so deterministic")
check(claims(read_chat(chat), "acme")[2] == ops, "the same transcript gives the same claims")

session = (HERE / "fixtures/session.jsonl").read_text().splitlines()
check(sniff(session[0]) == "claude-code", "a Claude Code transcript is recognised")
ev = read_claude_code(session, person="ana")
kinds = [(e.kind, e.author, e.tool) for e in ev]
check(kinds == [("message", "ana", None), ("action", "claude-code", "Read"), ("message", "claude-code", None),
                ("action", "claude-code", "WebFetch"), ("message", "ana", None)],
      f"the person's and the agent's text and the tool calls; thinking, tool results and reminders left out: {kinds}")
check(ev[1].used == ["file:src/client.py"] and ev[3].used == ["https://example.org/docs/retries"],
      "a file under the session's folder is named relative to it; a URL as itself")

grown = chat + ['{"id": "6", "conversation": "acme-intake", "author": "Dana Reyes", "time": "2026-10-18T09:02:00Z", '
                '"text": "Sam signs off within two days."}']
_, sha2, ops2 = claims(read_chat(grown), "acme")
prior = [{"id": f"p{i}", "subject": op["subject"], "predicate": op["predicate"], "qualifiers": {}, "valid_from": None,
          "status": "current"} for i, op in enumerate(ops)]
sets = change_sets(ops2, {"op": "add_artifact", "sha256": sha2}, "interaction:acme|interview.jsonl|x|", 40, prior,
                   "urn:kk:agent:encoder-interaction", "urn:kk:scope:default")
deprecated = [op for cs in sets for op in cs["ops"] if op["op"] == "set_status"]
check(len(sets) > 1 and sets[0]["ops"][0]["op"] == "add_artifact" and len(deprecated) == len(prior)
      and all(op.get("reason") == "superseded" for op in deprecated),
      f"a grown transcript, in batches: each earlier claim superseded by its new version ({len(sets)} sets)")
print(f"M5-T3 interaction encoder checks passed ({ok}/{ok})")
