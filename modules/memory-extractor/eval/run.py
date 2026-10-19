"""M6-T4 eval: does the extractor take the expected memories from the fixture conversations?

Replays conversations.jsonl through the worker's prompt, one message at a time in order, each seeing the
memories taken so far (newest first, as the worker's conversation lookup does), and scores the result
against expected.yaml: expected memories found (a memory of that message whose sentence has every word;
one memory may hold several),
corrections that replace the right memory, standing instructions with an `always` cue, messages that
should give nothing, items dropped (not grounded in the message, or for an agent's message not a
commitment), and memories beyond the expected ones.

  python modules/memory-extractor/eval/run.py --label lfm [--answers answers.jsonl]

Answers are read from --answers when it has them, else asked (KK_EXTRACT_MODEL at KK_EXTRACT_URL, as the
worker) and appended to it, so a run can be scored again without a model. Scores go to runs/.
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from memory_extractor.extract import memories, parse, prompt  # noqa: E402

EARLIER_MAX = 8


def has(text: str, words: list[str]) -> bool:
    return all(any(w.lower() in text.lower() for w in alt.split("|")) for alt in words)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--answers", type=Path)
    a = ap.parse_args()
    cached = {}
    if a.answers and a.answers.exists():
        cached = {r["message"]: r for r in map(json.loads, a.answers.open())}
    messages = [json.loads(line) for line in (HERE / "conversations.jsonl").open()]
    expected = yaml.safe_load((HERE / "expected.yaml").read_text())
    taken: list[dict] = []   # every memory, with the message it came from
    models, items, dropped = set(), 0, 0
    previous: dict[str, dict] = {}
    for m in messages:
        earlier = [t for t in reversed(taken) if not t.get("replaced")][:EARLIER_MAX]
        if m["id"] in cached:
            answer, model = cached[m["id"]]["answer"], cached[m["id"]]["model"]
        else:
            from memory_extractor.__main__ import ask
            before = previous.get(m["conversation"])
            answer, model = ask(prompt(m["text"], m["author"], m["time"], before["text"] if before else "",
                                       before["author"] if before else "",
                                       [f"({t['when']}) {t['text']}" for t in earlier]))
            if a.answers:
                with a.answers.open("a") as f:
                    f.write(json.dumps({"message": m["id"], "answer": answer, "model": model}) + "\n")
        models.add(model)
        raw = parse(answer)
        got = memories(raw, m["text"], len(earlier), agent=m.get("kind") == "agent")
        items, dropped = items + len(raw), dropped + len(raw) - len(got)
        for g in got:
            old = earlier[g["replaces"] - 1] if g["replaces"] else None
            if old:
                old["replaced"] = True
            taken.append({**g, "message": m["id"], "when": m["time"][:10], "replaces_message": old and old["message"]})
        previous[m["conversation"]] = m
    hits, matched = [], set()
    for e in expected["memories"]:
        # One memory may hold several expected ones ("moved to Hobart and got a dog called Pip").
        found = next((i for i, t in enumerate(taken) if t["message"] == e["message"] and has(t["text"], e["words"])),
                     None)
        if found is not None:
            matched.add(found)
        t = taken[found] if found is not None else {}
        hits.append({"message": e["message"], "words": e["words"], "found": found is not None,
                     "replaces_ok": (t.get("replaces_message") == e["replaces"]) if "replaces" in e else None,
                     "always_ok": bool((t.get("cue") or {}).get("always")) if e.get("always") else None})
    quiet = [mid for mid in expected["none"] if not any(t["message"] == mid for t in taken)]
    rate = lambda xs: round(sum(xs) / len(xs), 3) if xs else None   # noqa: E731
    out = {"eval": "memory-extractor fixture", "label": a.label, "models": sorted(models), "date": str(date.today()),
           "metrics": {"expected_found": rate([h["found"] for h in hits]),
                       "corrections_replace": rate([h["replaces_ok"] for h in hits if h["replaces_ok"] is not None]),
                       "instructions_cued": rate([h["always_ok"] for h in hits if h["always_ok"] is not None]),
                       "quiet_messages": rate([mid in quiet for mid in expected["none"]]),
                       "items_dropped": rate([True] * dropped + [False] * (items - dropped)),
                       "extra_memories": len(taken) - len(matched)},
           "missed": [h for h in hits if not h["found"]],
           "memories": [{k: t[k] for k in ("message", "kind", "text", "replaces_message")} for t in taken]}
    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    (runs / f"{date.today()}-{re.sub(r'[^a-z0-9-]', '-', a.label.lower())}.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out["metrics"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
