"""The pre-tool hook (D-91, M6-T16): memories whose recall cue names the tool about to run, or the file it
touches. It runs before every tool call, so it reads no database and imports nothing heavy: the session's
cues come from the file the session-start and prompt hooks write (kernel-mcp-hook, KK_STATE_DIR).

  kernel-mcp-cue      reads Claude Code's PreToolUse JSON on stdin; prints additionalContext, or nothing
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import cues

STATE = Path(os.environ.get("KK_STATE_DIR") or Path.home() / ".local/state/knowledge-kernel")


def lines(data: dict) -> list[str]:
    from .briefing import cue_line
    import re
    path = STATE / (re.sub(r"[^A-Za-z0-9_.-]", "_", data.get("session_id") or "default") + ".cues.json")
    if not path.exists():
        return []
    saved = json.loads(path.read_text())
    tool_input = data.get("tool_input") or {}
    cwd = (data.get("cwd") or "").rstrip("/") + "/"
    paths = tuple(p.removeprefix(cwd) for p in (tool_input.get(k) for k in ("file_path", "path", "notebook_path"))
                  if isinstance(p, str))
    out = []
    for item in saved.get("cues", []):
        cue = cues.parse(item["cue"])
        if cue and (why := cues.fires(cue, tool=data.get("tool_name"), tool_input=tool_input, paths=paths)):
            out.append(cue_line(item["row"], why, saved.get("me")))
    return out


def main() -> int:
    try:
        raw = sys.stdin.read()
        found = lines(json.loads(raw) if raw.strip() else {})
        if found:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
                              "Knowledge kernel: memories for this action:\n" + "\n".join(found)}}))
    except Exception as e:  # never block the harness
        print(f"knowledge-kernel pre-tool hook: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
