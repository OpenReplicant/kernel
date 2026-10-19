"""Harness hooks: recall and recording without the agent having to ask (integration level 3, D-36).

  python -m kernel_mcp.hooks session-start   start a kernel run for the session; tell the agent its run
  python -m kernel_mcp.hooks prompt          recall claims related to the user's prompt
  python -m kernel_mcp.hooks session-end     finish the session's run

Each reads the harness's hook JSON on stdin (Claude Code: session_id, prompt, cwd, ...) and prints
Claude Code's hook JSON with `additionalContext` on stdout. KK_DATABASE_URL (or DATABASE_URL) is the
agent's own login, as for stdio kernel-mcp; hooks inherit the harness's environment. A hook never blocks the session: on any error it reports to stderr and exits 0.
Env: KK_RECALL_LIMIT (default 8), KK_STATE_DIR (default ~/.local/state/knowledge-kernel), KK_PROFILE (memory:
the session-start text names the memory profile's tools, M6-T1).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from . import briefing, cues, recall
from .server import PREFIXES, SWITCHING_LOGIN, database_url

STATE = Path(os.environ.get("KK_STATE_DIR") or Path.home() / ".local/state/knowledge-kernel")


def connect() -> psycopg.Connection:
    conn = psycopg.connect(database_url(), autocommit=True)
    if conn.execute(SWITCHING_LOGIN).fetchone()[0]:
        raise RuntimeError("DATABASE_URL must be the agent's own login, not one that can switch roles")
    return conn


def run_file(session_id: str) -> Path:
    return STATE / (re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "default") + ".run")


def cue_file(session_id: str) -> Path:
    return STATE / (re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "default") + ".cues.json")


def cued(conn, data: dict, **situation) -> list[str]:
    """Lines for the memories whose recall cue fires in this situation (D-91). Also refreshes the session's
    file of tool and path cues, which the pre-tool hook (kernel-mcp-cue) reads without a database."""
    me = conn.execute("select kernel.my_agent()").fetchone()[0]
    rows = conn.execute(briefing.CUED).fetchone()[0]
    lines, actions = [], []
    for r in rows:
        cue = cues.parse(r["cue"])
        if not cue:
            continue
        if why := cues.fires(cue, **situation):
            lines.append(briefing.cue_line(r, why, me))
        if cue.get("tool") or cue.get("where"):
            actions.append({"cue": cue, "row": r})
    STATE.mkdir(parents=True, exist_ok=True)
    cue_file(data.get("session_id", "")).write_text(json.dumps({"me": me, "cues": actions}))
    return lines


def context(event: str, text: str) -> None:
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


ENCODED = """select info->>'commit', started_at from kernel.run
             where module = 'urn:kk:module:self-encoder' and status = 'succeeded' and info->>'repo_name' = %s
             order by started_at desc limit 1"""


def freshness(conn, cwd: str | None) -> str:
    """Whether the kernel has read this checkout's commit (M3-G1): a line for the agent, or ''."""
    if not cwd:
        return ""
    try:
        head = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    if not head:
        return ""
    name = Path(cwd).name
    row = conn.execute(ENCODED, (name,)).fetchone()
    if row is None:
        return (f" The repository {name} is not in the kernel yet: ask the user to run `kk up {cwd}` so its "
                "packages and documents are recalled.")
    if row[0] != head:
        return (f" The kernel read {name} at commit {(row[0] or 'unknown')[:12]} ({row[1]:%Y-%m-%d %H:%M}); the "
                f"checkout is at {head[:12]}: claims about it may be stale; `kk up {cwd}` refreshes them.")
    return ""


def session_start(data: dict) -> None:
    with connect() as conn:
        agent = conn.execute("select kernel.my_agent()").fetchone()[0]
        run = conn.execute("select api.start_run(null, null, '[]'::jsonb, %s)",
                           (Jsonb({"harness_session": data.get("session_id"), "cwd": data.get("cwd"),
                                   "source": data.get("source")}),)).fetchone()[0]
        visible = conn.execute("select count(*) from api.claims where status <> 'deprecated'").fetchone()[0]
        fresh = freshness(conn, data.get("cwd"))
    STATE.mkdir(parents=True, exist_ok=True)
    run_file(data.get("session_id", "")).write_text(str(run))
    if os.environ.get("KK_PROFILE") == "memory":
        with connect() as conn:
            standing = cued(conn, data, start=True, today=date.today(), paths=(data.get("cwd") or "",))
        context("SessionStart", ("Knowledge kernel: memories to keep in mind now:\n" + "\n".join(standing) + "\n\n"
                                 if standing else "") +
                f"Knowledge kernel connected as {agent} ({visible} claims visible): your memory, with sources. "
                "Memories related to each prompt are recalled for you; recall(question) asks for more. Each line "
                "says who said it and its belief, and ends in a #handle: cite the handles you rely on, and call "
                "explain(handle) before relying on a line you did not write. remember(kind, text) records one "
                "sentence (a fact, decision, preference, commitment or correction); a line in your reply that "
                "starts with Decision: or Remember: is recorded from the session transcript too. "
                "Give remember a when= cue (always, mentions, a date, a path, a tool) for what must come back "
                "whatever the ranking. changes_since() says what others changed." + fresh)
        return
    context("SessionStart",
            f"Knowledge kernel connected as {agent} ({visible} claims visible). This session's kernel run is "
            f"{run}: use it as generated_by when you submit change sets. Claims related to each prompt are "
            "recalled for you; cite claim ids and their belief, and call explain(claim_id) before relying "
            "on a claim you did not make. Record what you learn as claims (knowledge-kernel skill)." + fresh)


def prompt(data: dict) -> None:
    text = (data.get("prompt") or "").strip()
    if not text:
        return
    limit = int(os.environ.get("KK_RECALL_LIMIT", "8"))
    with connect() as conn:
        claims = recall.recall(conn, text, limit)
        mentioned = cued(conn, data, text=text) if os.environ.get("KK_PROFILE") == "memory" else []
    context("UserPromptSubmit", "\n\n".join(filter(None, [
        "Knowledge kernel: memories this prompt calls up:\n" + "\n".join(mentioned) if mentioned else "",
        recall.render(claims, PREFIXES, "Knowledge kernel: claims related to this prompt (cite ids and belief; "
                                        "explain(claim_id) gives sources):")])))


def session_end(data: dict) -> None:
    path = run_file(data.get("session_id", ""))
    if not path.exists():
        return
    with connect() as conn:
        conn.execute("select api.finish_run(%s::uuid, 'succeeded', %s)",
                     (path.read_text().strip(), Jsonb({"reason": data.get("reason")})))
    path.unlink()
    cue_file(data.get("session_id", "")).unlink(missing_ok=True)


HOOKS = {"session-start": session_start, "prompt": prompt, "session-end": session_end}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in HOOKS:
        print(f"usage: python -m kernel_mcp.hooks {{{','.join(HOOKS)}}}", file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.read()
        HOOKS[sys.argv[1]](json.loads(raw) if raw.strip() else {})
    except Exception as e:  # never block the harness
        print(f"knowledge-kernel hook {sys.argv[1]}: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
