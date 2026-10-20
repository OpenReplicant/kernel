"""The maps -> what they lack, as the questions to ask next (ported from modules/map-gaps, D-79).

Input: statements (s, relation, o) and the entities on the map. A gap is a question:

  no-performer   a step nobody does                     "Who does “Check the budget code”?"
  no-next        a step with nothing after it (or the process ending after it)
  no-previous    a step nothing leads to (or the process starting with it)
  unheld         a post nobody holds
  no-manager     a post that reports to no one while another post does not either (one top post is the head)
  no-unit        a post in no team or organisation
"""
from __future__ import annotations

from collections import defaultdict

ORDER = ["no-performer", "no-next", "no-previous", "unheld", "no-manager", "no-unit"]


def gaps(statements: list[dict], entities: dict[str, dict]) -> list[dict]:
    """[{kind, target, text}], in the order a person would be asked."""
    by = defaultdict(lambda: defaultdict(set))   # relation -> subject -> objects
    for s in statements:
        by[s["relation"]][s["s"]].add(s["o"])
    name = lambda k: entities[k]["name"]  # noqa: E731
    steps = sorted(k for k, e in entities.items() if e["kind"] == "step")
    posts = sorted(k for k, e in entities.items() if e["kind"] == "post")
    after = {o for objs in by["next"].values() for o in objs} | {o for objs in by["starts_with"].values() for o in objs}
    last = {o for objs in by["ends_after"].values() for o in objs}
    held = {o for objs in by["holds"].values() for o in objs}
    out = []
    for s in steps:
        if not by["performed_by"].get(s):
            out.append(("no-performer", s, f"Who does “{name(s)}”?"))
        if not by["next"].get(s) and s not in last:
            out.append(("no-next", s, f"What happens after “{name(s)}”, or does the process end there?"))
        if s not in after:
            out.append(("no-previous", s, f"What comes before “{name(s)}”, or does the process start there?"))
    tops = [p for p in posts if not by["reports_to"].get(p)]
    for p in posts:
        if p not in held:
            out.append(("unheld", p, f"Who holds the post of {name(p).lower()}?"))
        if p in tops and len(tops) > 1:
            out.append(("no-manager", p, f"Whom does the {name(p).lower()} report to?"))
        if not by["post_in"].get(p):
            out.append(("no-unit", p, f"Which team or organisation is the {name(p).lower()} part of?"))
    rank = {k: i for i, k in enumerate(ORDER)}
    return [{"kind": k, "target": t, "text": x} for k, t, x in sorted(out, key=lambda g: (rank[g[0]], g[1]))]
