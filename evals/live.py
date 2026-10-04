"""`make live`: the MVP check with a real harness and model.

Headless Claude Code is connected to a running gateway (`make up`, interactive profile) with
the core and interview skills installed and only the seven kernel tools allowed. It maps a document and then
a conversation, one turn at a time, and answers a question with citations. The resulting graph
is checked loosely (the model's wording varies) and the log is replayed.

Needs the `claude` CLI with model access. Each run costs model usage (about three US dollars for
the northwind scenario with the interview skill), so it is not part of CI. A scenario may
score the graph against a CI fixture's expected graph (`score_against`) and run SQL checks.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import psycopg
import yaml

from evals.compare import compare, norm, produced_graph
from evals.player import fixtures
from evals.replay import replay
from kernel import admin

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "evals" / "live"
REPORT = ROOT / "evals" / "out" / "live.json"
TOOLS = [
    "mcp__wmk__write",
    "mcp__wmk__lookup_entities",
    "mcp__wmk__get_schema_slice",
    "mcp__wmk__query_graph",
    "mcp__wmk__query_log",
    "mcp__wmk__ingest_source",
    "mcp__wmk__cite",
    "Skill",
]
DENIED = ["Bash", "Edit", "Write", "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Task", "NotebookEdit"]

# Defaults for scenarios that do not set their own skills, notes or document prompt.
DEFAULT_SKILLS = ["skills/core", "skills/interview"]
DEFAULT_NOTES = """You are the assistant in an interactive World Model Kernel session
(ACP profile `interactive`). Use the world-model-core skill for every turn, and the
world-model-interview skill while the person talks. The wmk MCP
server's instructions name your agent id and the person's agent id. This session's
conversation id is `{session}`.
Business-process facts go in the `bpm` namespace (reference pack bpm-reference).
Keep replies to the person short: say what you recorded, and anything that was rejected or contested.
"""
DEFAULT_DOCUMENT_PROMPT = (
    "Before the interview: ingest this document (title '{title}', media type text/plain, "
    "not part of the conversation) and map it into the world model.\n\n{content}"
)


def skill_name(folder: Path) -> str:
    """The skill's name from its SKILL.md frontmatter: harnesses expect it as the folder name."""
    front = (folder / "SKILL.md").read_text().split("---")[1]
    return str(yaml.safe_load(front)["name"])


def workspace(url: str, scenario: dict[str, Any]) -> Path:
    """A throwaway project for the harness: the gateway as its only MCP server, the scenario's skills."""
    path = Path(tempfile.mkdtemp(prefix="wmk-live-"))
    (path / ".mcp.json").write_text(json.dumps({"mcpServers": {"wmk": {"type": "http", "url": url}}}))
    notes = scenario.get("notes", DEFAULT_NOTES)
    (path / "CLAUDE.md").write_text(notes.format(session=scenario["session"]))
    for folder in scenario.get("skills", DEFAULT_SKILLS):
        shutil.copytree(ROOT / folder, path / ".claude" / "skills" / skill_name(ROOT / folder))
    return path


def document_prompt(scenario: dict[str, Any], document: dict[str, Any]) -> str:
    """The prompt handing the harness one document. `{ingest}` is the document's ingest_source
    arguments as JSON (as the research pack's get_paper returns them), `{content}` its text."""
    ingest = {
        k: v for k, v in document.items() if k in ("title", "media_type", "uri", "collection", "metadata")
    }
    template = scenario.get("document_prompt", DEFAULT_DOCUMENT_PROMPT)
    return template.format(
        title=document["title"], content=document["content"], ingest=json.dumps(ingest, ensure_ascii=False)
    )


def turn(cwd: Path, prompt: str, session: str | None, max_steps: int) -> dict[str, Any]:
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--mcp-config",
        str(cwd / ".mcp.json"),
        "--strict-mcp-config",
        "--allowedTools",
        *TOOLS,
        "--disallowedTools",
        *DENIED,
        "--max-turns",
        str(max_steps),
    ]
    if session:
        cmd += ["--resume", session]
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1800, check=False)
    if done.returncode != 0 and not done.stdout.strip():
        raise RuntimeError(f"claude exited {done.returncode}: {done.stderr[-500:]}")
    return json.loads(done.stdout)


def check(dsn: str, checks: dict[str, Any]) -> list[str]:
    failures = []
    with psycopg.connect(dsn) as conn:
        if role := checks.get("contested_role"):
            rows = conn.execute(
                "SELECT e.belief_status FROM kernel.edges e JOIN kernel.nodes r ON r.id = e.to_id "
                "WHERE e.edge = 'implements' AND kernel.normalize_name(r.name) = kernel.normalize_name(%s)",
                [role],
            ).fetchall()
            if len(rows) < 2 or any(r[0] != "contested" for r in rows):
                failures.append(
                    f"{role}: expected two or more holders, all contested; got {[r[0] for r in rows]}"
                )
        if minimum := checks.get("min_process_parts"):
            best = conn.execute(
                "SELECT coalesce(max(n), 0) FROM (SELECT count(*) n FROM kernel.edges e JOIN kernel.nodes p "
                "ON p.id = e.to_id WHERE e.edge = 'part_of' AND p.kind = 'process' GROUP BY p.id) x"
            ).fetchone()
            if not best or best[0] < minimum:
                failures.append(
                    f"no process with {minimum} or more parts (largest has {best[0] if best else 0})"
                )
        if member := checks.get("membership"):
            found = conn.execute(
                "SELECT count(*) FROM kernel.edges e JOIN kernel.nodes a ON a.id = e.from_id "
                "JOIN kernel.nodes t ON t.id = e.to_id WHERE e.edge = 'part_of' AND a.type = 'Agent' "
                "AND kernel.normalize_name(a.name) = kernel.normalize_name(%s) "
                "AND kernel.normalize_name(t.name) LIKE '%%' || kernel.normalize_name(%s) || '%%'",
                [member["member"], member["of"]],
            ).fetchone()
            if not found or not found[0]:
                failures.append(f"{member['member']} is not part_of {member['of']}")
        for named in checks.get("sql", []):
            row = conn.execute(named["sql"]).fetchone()
            value = row[0] if row else None
            wanted = {k: named[k] for k in ("eq", "min", "max") if k in named}
            if value is None or (
                ("eq" in wanted and value != wanted["eq"])
                or ("min" in wanted and value < wanted["min"])
                or ("max" in wanted and value > wanted["max"])
            ):
                failures.append(f"{named['name']}: got {value}, expected {wanted}")
        if checks.get("fully_cited_answer"):
            answers = conn.execute(
                "SELECT answer_id, bool_and(cardinality(assertion_ids) > 0) FROM kernel.cites "
                "GROUP BY answer_id"
            ).fetchall()
            if not any(traced for _, traced in answers):
                failures.append("no answer with every sentence traced to assertions")
    return failures


def entity_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """A graph without its Claim nodes and their edges."""
    claims = {norm(e["name"]) for e in graph["entities"] if e["type"] == "Claim"}
    return {
        "entities": [e for e in graph["entities"] if e["type"] != "Claim"],
        "edges": [e for e in graph["edges"] if norm(e["from"]) not in claims and norm(e["to"]) not in claims],
        "unresolved_claims": graph.get("unresolved_claims", 0),
    }


def score(dsn: str, fixture_name: str) -> dict[str, Any]:
    """Precision and recall against a CI fixture's expected graph, on the part a model's wording
    cannot change: entities and the edges between them. Claim nodes are named by the model's own
    sentences, so findings and their edges are judged by the scenario's checks instead."""
    fixture = fixtures([fixture_name])[0]
    c = compare(entity_graph(fixture.expected), entity_graph(produced_graph(dsn)))
    return {
        "against": fixture_name,
        "entities": {
            "precision": c.entities.precision,
            "recall": c.entities.recall,
            "missing": c.entities.missing,
            "unexpected": c.entities.unexpected,
        },
        "edges": {
            "precision": c.edges.precision,
            "recall": c.edges.recall,
            "missing": c.edges.missing,
            "unexpected": c.edges.unexpected,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a live-harness scenario against a running gateway.")
    parser.add_argument("scenario", nargs="?", default="northwind")
    parser.add_argument("--url", default="http://localhost:8000/mcp")
    parser.add_argument("--database", default="wmk")
    parser.add_argument("--max-steps", type=int, default=80, help="harness steps per turn")
    parser.add_argument("--allow-existing", action="store_true", help="run on a database that has data")
    args = parser.parse_args()
    if not shutil.which("claude"):
        sys.exit("the claude CLI is required for the live check")

    scenario = yaml.safe_load((SCENARIOS / f"{args.scenario}.yaml").read_text())
    dsn = admin.dsn_for(admin.admin_dsn(), args.database)
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT count(*) FROM kernel.nodes WHERE type <> 'Agent'").fetchone()
    if row and row[0] and not args.allow_existing:
        sys.exit(
            f"{args.database} already holds {row[0]} nodes; start from a fresh stack (make down up) "
            "or pass --allow-existing"
        )
    cwd = workspace(args.url, scenario)
    turn_prefix = scenario.get("turn_prefix", "[Turn {n} from the person]\n")
    for document in scenario.get("documents", []):
        if "file" in document:
            document["content"] = (ROOT / document.pop("file")).read_text()
    prompts = [document_prompt(scenario, d) for d in scenario.get("documents", [])] + [
        turn_prefix.format(n=i) + text for i, text in enumerate(scenario.get("turns", []), start=1)
    ]

    session = None
    transcript = []
    for prompt in prompts:
        started = time.time()
        result = turn(cwd, prompt, session, args.max_steps)
        session = result.get("session_id", session)
        transcript.append(
            {
                "prompt": prompt,
                "reply": result.get("result"),
                "steps": result.get("num_turns"),
                "cost_usd": result.get("total_cost_usd"),
                "seconds": round(time.time() - started),
                "error": result.get("is_error"),
            }
        )
        print(f"--- {transcript[-1]['seconds']}s, {transcript[-1]['steps']} steps\n{result.get('result')}\n")

    failures = [f"turn {i} ended in an error" for i, t in enumerate(transcript) if t["error"]]
    failures += check(dsn, scenario.get("checks", {}))
    scored = score(dsn, scenario["score_against"]) if scenario.get("score_against") else None
    if scored:
        floors = scenario.get("score_floors", {})
        for part in ("entities", "edges"):
            for measure in ("precision", "recall"):
                floor = floors.get(part, {}).get(measure)
                if floor is not None and scored[part][measure] < floor:
                    failures.append(f"{part} {measure} {scored[part][measure]:.2f} < {floor}")
    entries, diff = replay(args.database)
    failures += [f"replay: {line}" for line in diff[:10]]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {
                "scenario": args.scenario,
                "transcript": transcript,
                "failures": failures,
                "replayed_entries": entries,
                "score": scored,
            },
            indent=2,
        )
    )
    print(f"replay: {entries} entries, {'graph reproduced exactly' if not diff else 'DIFF'}")
    if scored:
        print(
            f"against {scored['against']}: entities P {scored['entities']['precision']:.2f} "
            f"R {scored['entities']['recall']:.2f}, edges P {scored['edges']['precision']:.2f} "
            f"R {scored['edges']['recall']:.2f}"
        )
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"report: {REPORT.relative_to(ROOT)}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
