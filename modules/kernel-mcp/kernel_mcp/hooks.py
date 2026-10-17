"""Harness hooks: recall and recording without the agent having to ask (integration level 3, D-36).

  python -m kernel_mcp.hooks session-start   start a kernel run for the session; tell the agent its run
  python -m kernel_mcp.hooks prompt          recall claims related to the user's prompt
  python -m kernel_mcp.hooks session-end     finish the session's run

Each reads the harness's hook JSON on stdin (Claude Code: session_id, prompt, cwd, ...) and prints
Claude Code's hook JSON with `additionalContext` on stdout. KK_DATABASE_URL (or DATABASE_URL) is the
agent's own login, as for stdio kernel-mcp; hooks inherit the harness's environment. A hook never blocks the session: on any error it reports to stderr and exits 0.
Env: KK_RECALL_LIMIT (default 8), KK_STATE_DIR (default ~/.local/state/knowledge-kernel).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from . import recall
from .server import PREFIXES, SWITCHING_LOGIN, database_url

STATE = Path(os.environ.get("KK_STATE_DIR") or Path.home() / ".local/state/knowledge-kernel")


def connect() -> psycopg.Connection:
    conn = psycopg.connect(database_url(), autocommit=True)
    if conn.execute(SWITCHING_LOGIN).fetchone()[0]:
        raise RuntimeError("DATABASE_URL must be the agent's own login, not one that can switch roles")
    return conn


def run_file(session_id: str) -> Path:
    return STATE / (re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "default") + ".run")


def context(event: str, text: str) -> None:
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


def session_start(data: dict) -> None:
    with connect() as conn:
        agent = conn.execute("select kernel.my_agent()").fetchone()[0]
        run = conn.execute("select api.start_run(null, null, '[]'::jsonb, %s)",
                           (Jsonb({"harness_session": data.get("session_id"), "cwd": data.get("cwd"),
                                   "source": data.get("source")}),)).fetchone()[0]
        visible = conn.execute("select count(*) from api.claims where status <> 'deprecated'").fetchone()[0]
    STATE.mkdir(parents=True, exist_ok=True)
    run_file(data.get("session_id", "")).write_text(str(run))
    context("SessionStart",
            f"Knowledge kernel connected as {agent} ({visible} claims visible). This session's kernel run is "
            f"{run}: use it as generated_by when you submit change sets. Claims related to each prompt are "
            "recalled for you; cite claim ids and their belief, and call explain(claim_id) before relying "
            "on a claim you did not make. Record what you learn as claims (knowledge-kernel skill).")


def prompt(data: dict) -> None:
    text = (data.get("prompt") or "").strip()
    if not text:
        return
    limit = int(os.environ.get("KK_RECALL_LIMIT", "8"))
    with connect() as conn:
        claims = conn.execute(*recall.plan(conn, text, limit)).fetchone()[0]
    context("UserPromptSubmit", recall.render(
        claims, PREFIXES, "Knowledge kernel: claims related to this prompt (cite ids and belief; "
                          "explain(claim_id) gives sources):"))


def session_end(data: dict) -> None:
    path = run_file(data.get("session_id", ""))
    if not path.exists():
        return
    with connect() as conn:
        conn.execute("select api.finish_run(%s::uuid, 'succeeded', %s)",
                     (path.read_text().strip(), Jsonb({"reason": data.get("reason")})))
    path.unlink()


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
