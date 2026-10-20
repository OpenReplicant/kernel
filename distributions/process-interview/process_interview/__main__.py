"""Record an interview into Cognee and print the maps, what they lack, and each process as BPMN.

  python -m process_interview TRANSCRIPT.jsonl --dataset acme [--answers ANSWERS.jsonl] [--bpmn DIR] [--fresh]

TRANSCRIPT has one message per line: {"id", "conversation", "author", "kind": "person" | "agent", "time",
"text"}. Each person's answer goes to the model with the question before it and what is already on the
map (Cognee's LLM settings: LLM_PROVIDER, LLM_MODEL, LLM_ENDPOINT, LLM_API_KEY); code checks the facts
(extract.check) and stores what holds up. Answers are read from --answers when it has them, else asked and
appended to it, so a run can be replayed without a model.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


async def ask(system: str, user: str) -> list[dict]:
    from cognee.infrastructure.llm.LLMGateway import LLMGateway
    from .extract import Facts
    try:
        out = await LLMGateway.acreate_structured_output(user, system, Facts)
    except Exception as e:   # a small model that cannot keep to the schema gives nothing for this answer
        print(f"  model failed: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return []
    return [f.model_dump() for f in out.facts]


async def store(dataset: str, pts: list) -> None:
    import cognee
    from cognee.modules.pipelines import Task
    from cognee.tasks.storage import add_data_points

    async def given(_):
        return pts
    await cognee.run_custom_pipeline(tasks=[Task(given), Task(add_data_points)], data=["answer"], dataset=dataset,
                                     pipeline_name="process_interview", skip_connection_test=True)


async def record(messages: list[dict], dataset: str, answers: Path | None, log=print) -> dict:
    from .extract import Map, check, prompt
    from .graph import points, read
    cached = {}
    if answers and answers.exists():
        cached = {(r["conversation"], r["message"]): r["facts"] for r in map(json.loads, answers.open())}
    entities, statements = await read(dataset)
    known = Map()
    known.entities.update(entities)
    for s in statements:
        if s["relation"] == "holds":
            known.holds.setdefault(s["s"], set()).add(s["o"])
    question: dict[str, str] = {}
    report = {"recorded": [], "dropped": []}
    for m in messages:
        if m.get("kind") == "agent":
            question[m["conversation"]] = m["text"]
            continue
        key = (m["conversation"], m["id"])
        if key in cached:
            facts = cached[key]
        else:
            facts = await ask(*prompt(m["text"], m["author"], question.get(m["conversation"], ""), known.lines()))
            if answers:
                with answers.open("a") as f:
                    f.write(json.dumps({"conversation": m["conversation"], "message": m["id"], "facts": facts}) + "\n")
        kept, dropped = check(facts, m["text"], m["author"], known)
        if kept:
            await store(dataset, points(dataset, m, kept))
        report["recorded"] += [{**s, "message": m["id"]} for s in kept]
        report["dropped"] += [{**d, "message": m["id"]} for d in dropped]
        log(f"[{m['id']}] {m['author']}: {m['text']}")
        for s in kept:
            log(f"    + {s['subject']} {s['relation']} {s['object']}   “{s['quote']}”")
        for d in dropped:
            log(f"    - dropped ({d['why']}): {d['subject']} {d['relation']} {d['object']}")
    return report


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("transcript", type=Path)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--answers", type=Path)
    ap.add_argument("--bpmn", type=Path, help="write each process as <DIR>/<process>.bpmn")
    ap.add_argument("--fresh", action="store_true", help="forget the dataset first")
    a = ap.parse_args()
    import cognee
    from cognee.modules.engine.operations.setup import setup
    from .bpmn import bpmn
    from .gaps import gaps
    from .graph import read
    await setup()
    if a.fresh:
        try:
            await cognee.forget(dataset=a.dataset)
        except Exception:   # nothing to forget yet
            pass
    messages = [json.loads(line) for line in a.transcript.open() if line.strip()]
    await record(messages, a.dataset, a.answers)
    entities, statements = await read(a.dataset)
    print("\nOpen questions:")
    for g in gaps(statements, entities):
        print(f"  {g['kind']}: {g['text']}")
    if a.bpmn:
        a.bpmn.mkdir(parents=True, exist_ok=True)
        for k, e in entities.items():
            if e["kind"] == "process":
                (a.bpmn / f"{k.split(':', 1)[1]}.bpmn").write_text(bpmn(k, entities, statements))
                print(f"wrote {a.bpmn / (k.split(':', 1)[1] + '.bpmn')}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
