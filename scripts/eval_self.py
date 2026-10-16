"""Self-questions eval (T16, gate G7): eval/self-model/questions.yaml, answered by an agent over MCP.

  python scripts/eval_self.py                     the reference agent: answers each question with MCP
                                                  query_claims from its evidence pattern (deterministic;
                                                  shows the self-model holds every answer, citable)
  python scripts/eval_self.py --grade FILE        grade a live agent's answers: JSON [{id, answer}]
  python scripts/eval_self.py --claude            ask Claude Code headless (claude -p) with the kernel
                                                  MCP server over stdio, then grade its answers

G7: >= 90% correct, every cited claim id resolves with `explain`, and belief shown. Answers are read and
explained over MCP stdio as the agent's own login. Env: AGENT_DATABASE_URL (or KK_DATABASE_URL).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "eval/self-model/questions.yaml"
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
BELIEF = re.compile(r"belief[^0-9]{0,20}(0(\.\d+)?|1(\.0+)?)\b", re.I)


def login() -> str:
    url = os.environ.get("AGENT_DATABASE_URL") or os.environ.get("KK_DATABASE_URL")
    if not url:
        sys.exit("set AGENT_DATABASE_URL to the agent's own login")
    return url


def session():
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME")}
    env["KK_DATABASE_URL"] = login()
    return stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "kernel_mcp"], env=env))


async def tool(s, name, args):
    res = await s.call_tool(name, args)
    text = res.content[0].text if res.content else ""
    if res.is_error:
        raise RuntimeError(text)
    sc = res.structured_content
    return sc.get("result", sc) if sc else json.loads(text)


def local(iri: str) -> str:
    return re.split(r"[#/:]", str(iri).rstrip("/#"))[-1]


def expected(q: dict) -> list[str]:
    if "expect_from" in q:
        path, key = q["expect_from"].rsplit(":", 1)
        return [str(yaml.safe_load((ROOT / path).read_text())[key])]
    return [str(e) for e in q["expect"]]


async def reference(questions: list[dict]) -> list[dict]:
    answers = []
    async with session() as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        for q in questions:
            ev = dict(q["evidence"])
            value = ev.pop("value", None)
            rows = await tool(s, "query_claims", ev)
            if value is not None:
                rows = [c for c in rows if (c["object_value"] or {}).get("@value") == value]
            side = (lambda c: local(c["subject"])) if q["answer"] == "subject" else \
                   (lambda c: local(c["object_entity"]) if c["object_entity"] else str(c["object_value"]["@value"]))
            parts = [f"{side(c)} (claim {c['id']}, belief {c['belief'] if c['belief'] is not None else 'none'})"
                     for c in rows]
            answers.append({"id": q["id"], "answer": "; ".join(parts) or "not found"})
    return answers


async def grade(questions: list[dict], answers: list[dict]) -> int:
    by_id = {a["id"]: a.get("answer", "") for a in answers}
    results = []
    async with session() as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        for q in questions:
            text = str(by_id.get(q["id"], ""))
            words = {w.strip(".,;:()[]\"'").lower() for w in re.split(r"[\s/#]+", text)} | \
                    {local(w).strip(".,;:()[]\"'").lower() for w in re.split(r"\s+", text) if ":" in w}
            cited = sorted(set(UUID.findall(text)))
            explained = []
            for cid in cited:
                try:
                    explained.append((await tool(s, "explain", {"claim_id": cid}))["claim"])
                except (RuntimeError, KeyError, TypeError):
                    explained.append(None)
            ok_cite = bool(cited) and all(explained)
            sides = lambda c: {local(c["subject"]).lower(),  # noqa: E731
                               (local(c["object_entity"]) if c.get("object_entity")
                                else str((c.get("object_value") or {}).get("@value"))).lower()}
            grounded = set().union(*(sides(c) for c in explained if c)) if any(explained) else set()
            pattern = q["evidence"]
            asserted = {(local(c["subject"]) if q["answer"] == "subject" else
                         local(c["object_entity"]) if c.get("object_entity") else str(c["object_value"]["@value"])).lower()
                        for c in explained if c and c["predicate"].endswith(pattern["predicate"].split(":")[-1])}
            correct = all(e.lower() in words and e.lower() in grounded for e in expected(q)) and \
                not any(x.lower() in asserted and x.lower() in words for x in q.get("exclude", []))
            shows_belief = bool(BELIEF.search(text))
            results.append((correct, ok_cite, shows_belief))
            print(("ok  " if correct and ok_cite and shows_belief else "FAIL"), q["id"], q["question"],
                  f"[correct={correct} cited={len(cited)} resolved={ok_cite}"
                  f" belief={shows_belief}]", text[:160].replace("\n", " "))
    n = len(questions)
    correct = sum(r[0] for r in results)
    cites = all(r[1] for r in results if r[0])
    belief = all(r[2] for r in results if r[0])
    passed = correct / n >= 0.9 and cites and belief
    print(f"G7 self-questions {'passed' if passed else 'FAILED'}: {correct}/{n} correct "
          f"({correct / n:.0%}), citations resolve: {cites}, belief shown: {belief}")
    return 0 if passed else 1


PROMPT = """You are connected to a knowledge kernel through the MCP server "kernel". Answer each question
below about the system's own structure using only the kernel's tools (search, query_claims, get_entity,
list_modules, list_slots, explain). For every answer cite the claim ids you relied on and show each
claim's belief, e.g. "validator-shacl (claim <uuid>, belief 0.50)". Reply with only a JSON array:
[{"id": "<question id>", "answer": "<answer with citations and beliefs>"}].

Questions:
"""


def claude(questions: list[dict]) -> list[dict]:
    config = {"mcpServers": {"kernel": {"command": sys.executable, "args": ["-m", "kernel_mcp"],
                                        "env": {"KK_DATABASE_URL": login()}}}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(config, f)
    prompt = PROMPT + "\n".join(f"- {q['id']}: {q['question']}" for q in questions)
    try:
        out = subprocess.run(["claude", "-p", prompt, "--mcp-config", f.name, "--strict-mcp-config",
                              "--allowedTools", "mcp__kernel", "--output-format", "json"],
                             capture_output=True, text=True, timeout=1800)
    finally:
        os.unlink(f.name)
    if out.returncode:
        sys.exit(f"claude -p failed: {(out.stderr or out.stdout)[-800:]}")
    result = json.loads(out.stdout)["result"]
    return json.loads(result[result.index("["):result.rindex("]") + 1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--grade", type=Path)
    ap.add_argument("--claude", action="store_true")
    ap.add_argument("--save", type=Path, help="write the answers here")
    a = ap.parse_args()
    questions = yaml.safe_load(QUESTIONS.read_text())["questions"]
    if a.grade:
        answers = json.loads(a.grade.read_text())
    elif a.claude:
        answers = claude(questions)
    else:
        answers = asyncio.run(reference(questions))
    if a.save:
        a.save.write_text(json.dumps(answers, indent=2) + "\n")
    return asyncio.run(grade(questions, answers))


if __name__ == "__main__":
    sys.exit(main())
