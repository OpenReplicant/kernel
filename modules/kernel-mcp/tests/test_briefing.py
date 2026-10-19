"""M6-T1 unit checks for briefings (no database): a memory is one line with its kind, words, author, belief
and handle; other claims read as labels, not IRIs; who said it is you, a person or an agent; the budget
is kept and what is left out is counted."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from kernel_mcp.briefing import AS, DCT, LABEL, NOTE, RDF_TYPE, fit, handle, lines, notes  # noqa: E402

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
print(f"M6-T1 briefing checks passed ({ok}/{ok})")
