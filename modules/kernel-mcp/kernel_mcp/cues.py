"""Recall cues (D-91, M6-T16): when a memory must come back whatever the ranking. Pure; standard library only.

A cue is a JSON object stored with a memory (kk:recallWhen). Each key is an alternative trigger:

  always    true                     at the start of every session (standing instructions, preferences)
  mentions  ["Sam", "payroll"]       when a prompt or question names one of these (whole words, any case)
  on        "2026-11-03" or "--05-30" when the date is near: within `before` days ahead (default 7);
            --MM-DD repeats every year (birthdays, anniversaries)
  where     "deploy/*"               when working under a matching path (the session's folder at its start;
                                     a file a tool is about to touch)
  tool      "Bash(git push*)"        before the agent uses a matching tool: a tool name glob, optionally
                                     with an argument glob in parentheses (Claude Code permission-rule style)
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from fnmatch import fnmatchcase

KEYS = ("always", "mentions", "on", "before", "where", "tool")
DATE = re.compile(r"^(\d{4}-\d{2}-\d{2}|--\d{2}-\d{2})$")
TOOL = re.compile(r"^([^()]+?)(?:\((.*)\))?$")


def check(cue: dict) -> dict:
    """The cue, normalised; ValueError when it is not one."""
    if not isinstance(cue, dict) or not cue:
        raise ValueError("a cue is an object with one or more of: " + ", ".join(KEYS))
    unknown = set(cue) - set(KEYS)
    if unknown:
        raise ValueError(f"unknown cue keys {sorted(unknown)}; use {', '.join(KEYS)}")
    out: dict = {}
    if cue.get("always"):
        out["always"] = True
    if "mentions" in cue:
        terms = [cue["mentions"]] if isinstance(cue["mentions"], str) else cue["mentions"]
        terms = [" ".join(str(t).split()) for t in terms if str(t).strip()]
        if not terms:
            raise ValueError("mentions needs at least one word or name")
        out["mentions"] = sorted(set(terms))
    if "on" in cue:
        if not DATE.match(str(cue["on"])):
            raise ValueError("on is a date YYYY-MM-DD, or --MM-DD for every year")
        when = str(cue["on"])
        date.fromisoformat(when if not when.startswith("--") else "2000" + when[1:])   # a real day
        out["on"] = when
        out["before"] = int(cue.get("before", 7))
        if not 0 <= out["before"] <= 366:
            raise ValueError("before is 0 to 366 days")
    elif "before" in cue:
        raise ValueError("before goes with on")
    for key in ("where", "tool"):
        if key in cue:
            if not str(cue[key]).strip():
                raise ValueError(f"{key} needs a pattern")
            out[key] = str(cue[key]).strip()
    if "tool" in out and not TOOL.match(out["tool"]):
        raise ValueError("tool is a name glob, optionally with an argument glob: Bash(git push*)")
    if not set(out) - {"before"}:
        raise ValueError("a cue needs always, mentions, on, where or tool")
    return out


def parse(value) -> dict | None:
    """A stored cue (JSON text, or already an object); None when it does not read as one."""
    try:
        return check(json.loads(value) if isinstance(value, str) else value)
    except (ValueError, TypeError):
        return None


def mentioned(text: str, terms: list[str]) -> str | None:
    low = " " + " ".join(re.split(r"[^\w]+", text.lower())) + " "
    for t in terms:
        if " " + " ".join(re.split(r"[^\w]+", t.lower())).strip() + " " in low:
            return t
    return None


def due(on: str, before: int, today: date) -> bool:
    if on.startswith("--"):
        month, day = int(on[2:4]), int(on[5:7])
        for year in (today.year, today.year + 1):
            try:
                target = date(year, month, day)
            except ValueError:   # 29 February in a common year: the 28th
                target = date(year, month, 28)
            if target >= today:
                return (target - today).days <= before
        return False
    target = date.fromisoformat(on)
    return timedelta(0) <= target - today <= timedelta(days=before)


def argument(tool_input: dict | None) -> str:
    """What a tool pattern's argument glob is matched against: the command, the file, else the input."""
    tool_input = tool_input or {}
    for key in ("command", "file_path", "path", "notebook_path", "url", "pattern"):
        if isinstance(tool_input.get(key), str):
            return tool_input[key]
    return json.dumps(tool_input, sort_keys=True)


def fires(cue: dict, *, start: bool = False, text: str | None = None, today: date | None = None,
          paths: tuple[str, ...] = (), tool: str | None = None, tool_input: dict | None = None) -> str | None:
    """Why the cue fires in this situation (shown with the memory), or None."""
    if start and cue.get("always"):
        return "always"
    if text and cue.get("mentions") and (term := mentioned(text, cue["mentions"])):
        return f"mentions {term}"
    if today and cue.get("on") and due(cue["on"], cue.get("before", 7), today):
        return f"on {cue['on']}"
    if cue.get("where"):
        for p in paths:
            if p and (fnmatchcase(p, cue["where"]) or fnmatchcase(p.lstrip("./"), cue["where"].lstrip("./"))):
                return f"where {cue['where']}"
    if tool and cue.get("tool"):
        name, arg = TOOL.match(cue["tool"]).groups()
        if fnmatchcase(tool, name.strip()) and (arg is None or fnmatchcase(argument(tool_input), arg)):
            return f"tool {cue['tool']}"
    return None
