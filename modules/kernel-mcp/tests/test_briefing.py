"""M6-T1 unit checks for briefings (no database): a memory is one line with its kind, words, author, belief
and handle; other claims read as labels, not IRIs; who said it is you, a person or an agent; the budget
is kept and what is left out is counted; a recalled message comes with its neighbours, grouped by conversation
in time order (D-92)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from kernel_mcp.briefing import AS, DCT, LABEL, NOTE, RDF_TYPE, fit, handle, lines, notes, recalled as excerpts  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


ME, ANA, ENC = "urn:kk:agent:claude-code", "urn:kk:agent:person:ana", "urn:kk:agent:encoder-bpmn"


def claim(i, s, p, o=None, v=None, by=ME, belief=0.5, status="normal"):
    return {"id": f"{i:08x}-0000-4000-8000-000000000000", "subject": s, "predicate": p, "object_entity": o,
            "object_value": {"@value": v} if v is not None else None, "qualifiers": {}, "attributed_to": by,
            "belief": belief, "status": status}


m = "urn:kk:memory:abc"
parts = [claim(1, m, RDF_TYPE, NOTE), claim(2, m, AS + "content", v="We pin psycopg 3.3.6"),
         claim(3, m, AS + "attributedTo", ME), claim(4, m, DCT + "type", "urn:kk:memory-kind:decision"),
         claim(5, m, DCT + "subject", "pkg:pypi/psycopg")]
found = notes(parts)
check(set(found) == {m} and found[m]["kind"] == "decision" and found[m]["about"] == ["pkg:pypi/psycopg"],
      "a note's parts: its kind and what it is about")
labels = {"pkg:pypi/psycopg": "psycopg", "urn:x:order": "Order process", "urn:x:approve": "Approve order"}
agents = {ME: {"kind": "software", "label": "claude-code"}, ANA: {"kind": "person", "label": "Ana"},
          ENC: {"kind": "software", "label": "BPMN encoder"}}
recalled = [parts[1], parts[3], claim(6, "urn:x:order", "https://example.org/bbo#has_step", "urn:x:approve", by=ENC,
                                      belief=0.8),
            claim(7, "urn:x:order", LABEL, v="Order process", by=ANA, belief=None, status="preferred")]
out = lines(recalled, found, labels, agents, ME)
check(len(out) == 3, "a memory is one line however many of its claims were recalled")
check(out[0] == f'- decision: "We pin psycopg 3.3.6" (about psycopg) — you; belief 0.50 {handle(parts[1]["id"])}',
      f"a memory: kind, words, topic, you, belief, the content claim's handle: {out[0]}")
check(out[1] == "- Order process · has_step · Approve order — BPMN encoder (agent); belief 0.80 #00000006",
      f"a claim between entities reads as labels, with the agent that made it: {out[1]}")
check(out[2] == '- Order process · label · "Order process" — Ana (person); unscored, preferred #00000007',
      f"a person's claim, unscored and preferred: {out[2]}")
said = notes([claim(8, "urn:kk:memory:x", RDF_TYPE, NOTE), claim(9, "urn:kk:memory:x", AS + "content", v="Ship Friday", by=ENC),
              claim(10, "urn:kk:memory:x", AS + "attributedTo", "urn:kk:actor:t:dana", by=ENC)])
line = lines([claim(9, "urn:kk:memory:x", AS + "content", v="Ship Friday", by=ENC)], said, {"urn:kk:actor:t:dana": "Dana"},
             agents, ME, {"urn:kk:actor:t:dana": AS + "Person"})[0]
check("— Dana (person);" in line, f"a memory read from a transcript is said by the person who wrote it: {line}")
many = [f"- line {i} " + "x" * 60 for i in range(100)]
brief = fit("Header", many, 200)
check(len(brief) <= 800 and brief.endswith("more not shown; ask with a larger budget)"),
      f"the budget is kept (about four characters a token) and the rest is counted: {len(brief)} characters")
check(fit("Header", ["- one"], 200) == "Header\n- one", "nothing is said about lines that fit")

def chat(i, conv, n, text, when, reply=None):
    s = f"urn:kk:message:t:{conv}:{n}"
    return [claim(i, s, RDF_TYPE, NOTE, by=ENC), claim(i + 1, s, AS + "content", v=text, by=ENC),
            claim(i + 2, s, AS + "attributedTo", "urn:kk:actor:t:dana", by=ENC),
            claim(i + 3, s, AS + "context", f"urn:kk:conversation:t:{conv}", by=ENC),
            {**claim(i + 4, s, AS + "published", by=ENC), "object_value": {"@value": when}},
            *([claim(i + 5, s, AS + "inReplyTo", f"urn:kk:message:t:{conv}:{reply}", by=ENC)] if reply else [])]


talk = chat(20, "b", 1, "We moved to Hobart", "2023-06-01T10:00:00Z") \
    + chat(30, "a", 1, "Where do you live?", "2023-01-05T09:00:00Z") \
    + chat(40, "a", 2, "In Perth, for now", "2023-01-05T09:01:00Z", 1) \
    + chat(50, "a", 3, "Nice", "2023-01-05T09:02:00Z", 2) + chat(60, "a", 4, "Unrelated", "2023-01-05T09:03:00Z", 3) \
    + chat(70, "a", 5, "Still in Perth", "2023-01-05T09:04:00Z", 4)
convo = notes(talk)
conv_labels = {"urn:kk:conversation:t:a": "Session A", "urn:kk:conversation:t:b": "Session B",
               "urn:kk:actor:t:dana": "Dana"}
hits = [convo[f"urn:kk:message:t:{c}:{n}"]["content"] for c, n in (("b", 1), ("a", 2), ("a", 5))]
near = {"urn:kk:message:t:a:2": ["urn:kk:message:t:a:1", "urn:kk:message:t:a:3"],
        "urn:kk:message:t:a:5": ["urn:kk:message:t:a:4"]}
out = excerpts([parts[1], *hits], near, {**found, **convo}, {**labels, **conv_labels}, agents, ME, {}, 4000)
check(out[0].startswith('- decision: "We pin psycopg') and out[1] == "- in Session A, from 2023-01-05:"
      and [x.split(": ", 1)[1].split(";")[0] for x in out[2:7]]
      == ['"Where do you live?"', '"In Perth, for now"', '"Nice"', '"Unrelated"', '"Still in Perth"']
      and out[7] == "- in Session B, from 2023-06-01:" and out[8].startswith('  - 2023-06-01 Dana: "We moved'),
      "a message comes with its neighbours; conversations oldest first, each in time order: " + " | ".join(out))
gap = excerpts([hits[1], hits[2]], {}, convo, conv_labels, agents, ME, {}, 4000)
check(gap[2] == "  [...]" and len(gap) == 4, "skipped messages are marked: " + " | ".join(gap))
small = excerpts([parts[1], *hits], near, {**found, **convo}, {**labels, **conv_labels}, agents, ME, {}, 300)
check(small[0].startswith("- decision") and small[-1].endswith("more not shown; ask with a larger budget)")
      and sum(len(x) + 1 for x in small[:-1]) <= 300, "lines are chosen in rank order within the room: " + " | ".join(small))
print(f"M6-T1 briefing checks passed ({ok}/{ok})")
