"""HumanEval (openai/human-eval, MIT), pinned by commit and sha256, cached under
$PC_DATA/bench/humaneval/<sha256>/ (docs/FILESYSTEM.md). A completion is checked by running
the problem's own tests in the sandbox; nothing a model wrote runs on the host."""
import gzip
import hashlib
import json
import re
import urllib.request
from pathlib import Path

from pclib import data_root, sandbox

COMMIT = "6d43fb980f9fee3c892a914eda09951f772ad10d"
URL = f"https://raw.githubusercontent.com/openai/human-eval/{COMMIT}/data/HumanEval.jsonl.gz"
SHA256 = "b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef"


def fetch() -> Path:
    """The dataset file, downloaded once and checked against the pinned hash."""
    d = data_root() / "bench" / "humaneval" / SHA256
    path = d / "HumanEval.jsonl.gz"
    if not path.exists():
        with urllib.request.urlopen(URL, timeout=60) as r:
            raw = r.read()
        got = hashlib.sha256(raw).hexdigest()
        if got != SHA256:
            raise ValueError(f"HumanEval download has sha256 {got}, expected {SHA256}")
        d.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o444)
    return path


def problems(path: Path | None = None) -> list[dict]:
    with gzip.open(path or fetch(), "rt") as f:
        return [json.loads(line) for line in f if line.strip()]


def spread(items: list, n: int) -> list:
    """n items evenly spaced through the list (deterministic, not just the first n)."""
    return [items[i * len(items) // n] for i in range(min(n, len(items)))]


def task_id(problem: dict) -> str:
    """HumanEval/12 -> HumanEval-12 (a step key segment can't hold a slash)."""
    return problem["task_id"].replace("/", "-")


PROMPT = ("Complete this Python function. Reply with the complete function, including its "
          "signature and any imports it needs, in one ```python code block.\n\n```python\n{prompt}```")


def extract_code(text: str) -> str:
    """The first fenced code block in a reply, or the whole reply if it has none."""
    m = re.search(r"```(?:python|py)?[ \t]*\n(.*?)```", text, re.S)
    return m.group(1) if m else text


def program(problem: dict, code: str) -> str:
    """The prompt (for its imports and helpers), the completion, the tests, and the call."""
    return f"{problem['prompt']}\n{code}\n\n{problem['test']}\n\ncheck({problem['entry_point']})\n"


def check(problem: dict, code: str, work: Path, timeout_s: float = 10) -> dict:
    r = sandbox.run(work, ["python", "check.py"], {"check.py": program(problem, code)}, timeout_s=timeout_s)
    return {**r, "passed": r["exit_code"] == 0 and not r["timed_out"]}
