"""Helpers for read-only Cypher over the AGE graph: linting, RETURN columns and agtype parsing.

Read-only is enforced three times: the reader role can only select, the query runs
in a READ ONLY transaction, and this module refuses updating clauses up front so the
model gets a clear problem document instead of a database error.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

UPDATING_CLAUSES = ("CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP", "LOAD", "CALL")
_KEYWORD = re.compile(r"\b(" + "|".join(UPDATING_CLAUSES) + r")\b", re.IGNORECASE)
_RETURN = re.compile(r"\bRETURN\b", re.IGNORECASE)
_TAIL = re.compile(r"\b(ORDER\s+BY|SKIP|LIMIT|UNION)\b", re.IGNORECASE)
_ALIAS = re.compile(r"\s+AS\s+`?([A-Za-z_][A-Za-z0-9_]*)`?\s*$", re.IGNORECASE)


class CypherError(ValueError):
    """A query the gateway refuses before it reaches the database."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def mask(query: str) -> tuple[str, list[int]]:
    """The query with string literals, backtick names and comments blanked, plus bracket depth per char.

    Blanking keeps character positions, so positions found in the mask apply to the query.
    """
    out: list[str] = []
    depth: list[int] = []
    level = 0
    i = 0
    n = len(query)
    while i < n:
        ch = query[i]
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(" ")
            depth.append(level)
            i += 1
            while i < n and query[i] != quote:
                if query[i] == "\\" and quote != "`" and i + 1 < n:
                    out.append(" ")
                    depth.append(level)
                    i += 1
                out.append(" ")
                depth.append(level)
                i += 1
            if i < n:
                out.append(" ")
                depth.append(level)
                i += 1
            continue
        if query.startswith("//", i):
            while i < n and query[i] != "\n":
                out.append(" ")
                depth.append(level)
                i += 1
            continue
        if query.startswith("/*", i):
            end = query.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.extend(" " * (end - i))
            depth.extend([level] * (end - i))
            i = end
            continue
        if ch in "([{":
            level += 1
        out.append(ch)
        depth.append(level)
        if ch in ")]}":
            level = max(0, level - 1)
        i += 1
    return "".join(out), depth


def check_read_only(query: str) -> None:
    masked, _ = mask(query)
    found = _KEYWORD.search(masked)
    if found:
        raise CypherError(
            "read_only",
            f"{found.group(1).upper()} is not allowed: query_graph is read-only; change the graph with write",
        )
    if ";" in masked:
        raise CypherError("query", "one Cypher statement per call, without ';'")


def return_columns(query: str) -> list[str]:
    """Column names of the final top-level RETURN clause, for AGE's column definition list."""
    masked, depth = mask(query)
    starts = [m for m in _RETURN.finditer(masked) if depth[m.start()] == 0]
    if not starts:
        raise CypherError("query", "the query needs a RETURN clause")
    begin = starts[-1].end()
    end = len(masked)
    for tail in _TAIL.finditer(masked, begin):
        if depth[tail.start()] == 0:
            end = tail.start()
            break
    seg = begin
    lead = masked[begin:end]
    if lead.lstrip().upper().startswith("DISTINCT"):
        seg = begin + (len(lead) - len(lead.lstrip())) + len("DISTINCT")
    items: list[str] = []
    last = seg
    for pos in range(seg, end):
        if masked[pos] == "," and depth[pos] == 0:
            items.append(query[last:pos])
            last = pos + 1
    items.append(query[last:end])
    names: list[str] = []
    for item in items:
        text = item.strip()
        if not text:
            raise CypherError("query", "empty item in RETURN")
        if text == "*":
            raise CypherError("query", "RETURN * is not supported; name the columns to return")
        alias = _ALIAS.search(text)
        name = alias.group(1) if alias else re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_") or "col"
        while name in names:
            name += "_"
        names.append(name)
    return names


def dollar_tag(query: str) -> str:
    """A dollar-quote tag that does not occur in the query, so the query cannot escape its quoting."""
    while True:
        tag = f"$wmk{secrets.token_hex(4)}$"
        if tag not in query:
            return tag


def parse_agtype(text: str | None) -> Any:
    """Parse AGE agtype output: JSON with '::vertex', '::edge', '::path', '::numeric' annotations."""
    if text is None:
        return None
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
            continue
        if text.startswith("::", i):
            j = i + 2
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            i = j
            continue
        out.append(ch)
        i += 1
    return simplify(json.loads("".join(out)))


def simplify(value: Any) -> Any:
    """Graph elements as kernel objects: AGE's internal graph ids dropped, kernel ids kept."""
    if isinstance(value, list):
        return [simplify(v) for v in value]
    if isinstance(value, dict) and {"id", "label", "properties"} <= value.keys():
        element = "edge" if "start_id" in value else "vertex"
        return {"element": element, "label": value["label"], **value["properties"]}
    if isinstance(value, dict):
        return {k: simplify(v) for k, v in value.items()}
    return value


def elements(value: Any) -> list[dict[str, Any]]:
    """Every vertex and edge inside a parsed result value."""
    found: list[dict[str, Any]] = []
    if isinstance(value, list):
        for v in value:
            found.extend(elements(v))
    elif isinstance(value, dict):
        if value.get("element") in ("vertex", "edge") and "id" in value:
            found.append(value)
        else:
            for v in value.values():
                found.extend(elements(v))
    return found
