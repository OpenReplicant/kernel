"""Change-set helpers for encoders, with no database dependency (unit-testable without psycopg).

  supersede(cs, prior)   deprecate an encoder's claims from an earlier version of a source: each is
                         superseded by the new claim with the same subject, predicate, qualifiers and
                         valid-from (D-48 e), otherwise retracted.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


def same(subject: str, predicate: str, qualifiers: dict | None, valid_from) -> str:
    """What makes a new claim the replacement of an earlier one."""
    if isinstance(valid_from, str):
        valid_from = datetime.fromisoformat(valid_from)
    vf = valid_from.astimezone(timezone.utc).isoformat() if valid_from else None
    return json.dumps([subject, predicate, qualifiers or {}, vf], sort_keys=True)


def supersede(cs: dict, prior: list[dict]) -> None:
    """Append set_status ops to `cs` deprecating `prior` (rows with id, subject, predicate, qualifiers,
    valid_from, status)."""
    new = {}
    for i, op in enumerate(cs["ops"]):
        if op["op"] == "add_claim":
            op["ref"] = f"c{i}"
            new.setdefault(same(op["subject"], op["predicate"], op.get("qualifiers"), op.get("valid_from")), op["ref"])
    for c in prior:
        by = new.get(same(c["subject"], c["predicate"], c["qualifiers"], c["valid_from"]))
        cs["ops"].append({"op": "set_status", "claim": str(c["id"]), "status": "deprecated", "expect_status": c["status"],
                          **({"reason": "superseded", "replaced_by": by} if by else {"reason": "retracted"})})


PRIOR = """select c.id, c.subject, c.predicate, c.qualifiers, c.valid_from, c.status
           from api.claims c join api.change_sets s on s.id = c.change_set
           where c.attributed_to = %s and c.status <> 'deprecated'
             and starts_with(s.idempotency_key, %s) and s.idempotency_key <> %s"""


def prior(cur, agent: str, key_prefix: str, key: str) -> list[dict]:
    """This agent's current claims from change sets whose key starts with `key_prefix`, except `key`."""
    cur.execute(PRIOR, (agent, key_prefix, key))
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
