"""Score a model's answers to the Acme interview: how much of the expected maps it gives, how much of what
it gives is right, and how many of its facts the checks drop. No Cognee needed: scoring replays the checks.

  python distributions/process-interview/eval/run.py ANSWERS.jsonl --label lfm-2.6b

ANSWERS is what `python -m process_interview ... --answers` wrote (or a sub-agent's answers to the
prompts that `--prompts` prints). Scores go to eval/runs/.
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from process_interview.extract import Map, check, prompt  # noqa: E402
from process_interview.gaps import gaps  # noqa: E402

FIXTURES = HERE.parent / "tests/fixtures"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("answers", type=Path, nargs="?")
    ap.add_argument("--label")
    ap.add_argument("--prompts", action="store_true",
                    help="print each answer's prompt, given the expected maps so far, as JSON lines")
    a = ap.parse_args()
    messages = [json.loads(line) for line in (FIXTURES / "acme.jsonl").open()]
    expected = {tuple(t) for t in json.load((FIXTURES / "acme.expected.json").open())}
    if a.prompts:   # each prompt sees the map a perfect model would have built so far, so they are independent
        scripted = {r["message"]: r["facts"] for r in map(json.loads, (FIXTURES / "acme.answers.jsonl").open())}
        known, question = Map(), ""
        for m in messages:
            if m["kind"] == "agent":
                question = m["text"]
                continue
            system, user = prompt(m["text"], m["author"], question, known.lines())
            print(json.dumps({"conversation": m["conversation"], "message": m["id"], "system": system, "user": user}))
            check(scripted[m["id"]], m["text"], m["author"], known)
        return 0
    answers = {r["message"]: r["facts"] for r in map(json.loads, a.answers.open())}
    known, kept, dropped, items = Map(), [], [], 0
    for m in messages:
        if m["kind"] == "agent":
            continue
        facts = answers.get(m["id"], [])
        items += len(facts)
        k, d = check(facts, m["text"], m["author"], known)
        kept += k
        dropped += [{**x, "message": m["id"]} for x in d]
    got = {(s["s"], s["relation"], s["o"]) for s in kept}
    right = got & expected
    out = {"eval": "process interview (acme)", "label": a.label, "date": str(date.today()),
           "metrics": {"recall": round(len(right) / len(expected), 3),
                       "precision": round(len(right) / len(got), 3) if got else None,
                       "facts": items, "dropped": len(dropped),
                       "open_questions_left": len(gaps(kept, known.entities))},
           "missed": sorted(expected - got), "wrong": sorted(got - expected),
           "dropped": [{k: d[k] for k in ("message", "subject", "relation", "object", "why")} for d in dropped]}
    if a.label:
        runs = HERE / "runs"
        runs.mkdir(exist_ok=True)
        (runs / f"{date.today()}-{re.sub(r'[^a-z0-9.-]', '-', a.label.lower())}.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out["metrics"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
