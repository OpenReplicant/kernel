"""Answer RCAEval evidence packs with a model: python eval/rcaeval/ask.py PROMPTS.jsonl ANSWERS.jsonl

Any OpenAI-compatible endpoint, as for SciFact (eval/scifact/run.py `ask`): KK_EVAL_MODEL_URL (a local
server such as Ollama's http://localhost:11434/v1 or llama.cpp's http://localhost:8080/v1), KK_EVAL_MODEL,
KK_EVAL_MODEL_KEY. Appends one line per case and skips cases already answered, so it can be stopped and
resumed. Score with run.py --method answers.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scifact"))
from run import ask  # noqa: E402


def main(prompts: str, answers: str) -> None:
    done = set()
    if Path(answers).exists():
        done = {json.loads(line)["key"] for line in open(answers)}
    with open(answers, "a") as out:
        for line in open(prompts):
            it = json.loads(line)
            if it["key"] in done:
                continue
            text, model = ask(it["prompt"])
            m = re.search(r"\{.*\}", text or "", re.S)
            try:
                ans = json.loads(m.group(0)) if m else {}
            except json.JSONDecodeError:
                ans = {}
            out.write(json.dumps({"key": it["key"], "model": model,
                                  "answer": {"ranks": ans.get("ranks", []), "evidence": ans.get("evidence", [])}}) + "\n")
            out.flush()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
