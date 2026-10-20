"""Process interview checks: the scripted Acme interview (a model that answers well) gives the expected maps,
the open questions after each answer are the ones the kernel's map gap processor listed (acme.yaml), facts
that do not hold up are dropped, and the BPMN parses. With --cognee the same interview goes through Cognee
and is read back from its graph, quotes included.

  python distributions/process-interview/tests/test_interview.py [--cognee]
"""
import asyncio
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from process_interview.bpmn import bpmn  # noqa: E402
from process_interview.extract import Map, check, parse  # noqa: E402
from process_interview.gaps import gaps  # noqa: E402

MESSAGES = [json.loads(line) for line in (HERE / "fixtures/acme.jsonl").open()]
ANSWERS = {r["message"]: r["facts"] for r in map(json.loads, (HERE / "fixtures/acme.answers.jsonl").open())}
# After each answer, the open questions (kind, target), as acme.yaml lists them for the kernel's interview.
OPEN = {
    "2": {("no-unit", "post:finance-director")},
    "4": {("no-next", "step:raise-a-purchase-order"), ("unheld", "post:buyer"), ("no-manager", "post:buyer"),
          ("no-manager", "post:finance-director"), ("no-unit", "post:buyer"), ("no-unit", "post:finance-director")},
    "6": {("no-next", "step:raise-a-purchase-order"), ("no-unit", "post:finance-director")},
    "8": {("no-unit", "post:finance-director")},
    "10": set(),
}
EXPECTED = {tuple(t) for t in json.load((HERE / "fixtures/acme.expected.json").open())}
failed = []


def ok(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f": {detail}" if detail and not cond else ""))
    if not cond:
        failed.append(name)


def scripted():
    known, statements = Map(), []
    for m in MESSAGES:
        if m["kind"] == "agent":
            continue
        kept, dropped = check(ANSWERS[m["id"]], m["text"], m["author"], known)
        ok(f"answer {m['id']}: every scripted fact holds up", not dropped, dropped)
        statements += kept
        got = {(g["kind"], g["target"]) for g in gaps(statements, known.entities)}
        ok(f"answer {m['id']}: open questions as the kernel's interview", got == OPEN[m["id"]], sorted(got ^ OPEN[m["id"]]))
    got = {(s["s"], s["relation"], s["o"]) for s in statements}
    ok("the maps are the expected ones", got == EXPECTED, sorted(got ^ EXPECTED))
    ok("every statement quotes its answer", all(s["quote"] for s in statements))
    return known, statements


def checks():
    known = Map()
    text = "I'm the procurement lead at Acme. I report to the finance director."
    F = lambda s, sk, r, o, ok_, q: dict(subject=s, subject_kind=sk, relation=r, object=o, object_kind=ok_, quote=q)  # noqa: E731
    kept, dropped = check([
        F("me", "person", "holds", "procurement lead", "post", "I'm the procurement lead"),
        F("procurement lead", "post", "reports_to", "finance director", "post", "I report to the CFO"),        # paraphrase
        F("procurement lead", "post", "reports_to", "head of sales", "post", "I report to the finance director"),  # not said
        F("procurement lead", "post", "holds", "Acme", "organisation", "at Acme"),                             # wrong kinds
        F("procurement lead", "post", "manages", "Acme", "organisation", "at Acme"),                            # no such relation
    ], text, "Dana Reyes", known)
    ok("a fact with the speaker is kept, as the speaker", [(s["s"], s["o"]) for s in kept] == [("person:dana-reyes", "post:procurement-lead")])
    ok("paraphrased quotes, unsaid names, wrong kinds and unknown relations are dropped",
       [d["why"] for d in dropped] == ["quote not in the answer", "a name not in the answer, or of the wrong kind",
                                        "a name not in the answer, or of the wrong kind", "unknown relation or kind"], dropped)
    ok("a fenced answer parses", parse('```json\n{"facts": [{"subject": "x"}]}\n```') == [{"subject": "x"}])
    ok("a broken answer gives nothing", parse('{"facts": [') == [])


def diagram(known, statements):
    xml = bpmn("process:purchasing", known.entities, statements)
    root = ET.fromstring(xml)
    ns = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
    tasks = [t.get("name") for t in root.iterfind(".//b:task", ns)]
    ok("BPMN: two tasks, a start and an end", sorted(tasks) == ["Check the budget code", "Raise a purchase order"]
       and root.find(".//b:startEvent", ns) is not None and root.find(".//b:endEvent", ns) is not None, tasks)
    ok("BPMN: three sequence flows", len(root.findall(".//b:sequenceFlow", ns)) == 3)
    ok("BPMN: a lane per post", sorted(l.get("name") for l in root.iterfind(".//b:lane", ns)) == ["Buyer", "Procurement lead"])
    ok("BPMN: tasks quote the interview", "“I check the budget code”" in xml)


async def through_cognee():
    tmp = tempfile.mkdtemp()
    os.environ.setdefault("SYSTEM_ROOT_DIRECTORY", f"{tmp}/system")
    os.environ.setdefault("DATA_ROOT_DIRECTORY", f"{tmp}/data")
    from cognee.modules.engine.operations.setup import setup
    from process_interview.__main__ import record
    from process_interview.graph import read
    await setup()
    half = [m for m in MESSAGES if int(m["id"]) <= 6]
    await record(half, "acme", HERE / "fixtures/acme.answers.jsonl", log=lambda *_: None)
    # A second session continues the same map: names from the first are known again (read back from Cognee).
    await record([m for m in MESSAGES if int(m["id"]) > 6], "acme", HERE / "fixtures/acme.answers.jsonl", log=lambda *_: None)
    entities, statements = await read("acme")
    got = {(s["s"], s["relation"], s["o"]) for s in statements}
    ok("Cognee: the maps read back are the expected ones", got == EXPECTED, sorted(got ^ EXPECTED))
    ok("Cognee: each relation keeps its quote, speaker and time",
       all(s.get("quote") and s.get("said_by") == "Dana Reyes" and s.get("at") for s in statements))
    ok("Cognee: no open questions left", gaps(statements, entities) == [], gaps(statements, entities))


if __name__ == "__main__":
    known, statements = scripted()
    checks()
    diagram(known, statements)
    if "--cognee" in sys.argv:
        asyncio.run(through_cognee())
    print("FAILED: " + ", ".join(failed) if failed else "ALL PROCESS INTERVIEW CHECKS PASSED")
    sys.exit(1 if failed else 0)
