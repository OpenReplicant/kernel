"""Model access from the graph (docs/MODELS.md): which model a role uses, what it costs, what
it has spent, and the record of each call.

Everything a call needs is a held claim (the `models` ontology, modules/models):

    (model_role)  models.uses -> (model), models.budget {usd?, tokens?}, models.params {...}
    (model)       models.model_id, models.served_by -> (endpoint), models.prices {...}, models.params {...}
    (endpoint)    models.api, models.base_url, models.key_env

A role's key is `<module>.<role>`. Swapping a model, a price or a budget is a superseding claim;
the code doesn't change.
"""
import json

from psycopg.types.json import Jsonb

from kernel import claims, db

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


class Unresolved(LookupError):
    pass


class OverBudget(RuntimeError):
    pass


def _thing(conn, label: str, key: str) -> str | None:
    row = conn.execute(f"MATCH (t:{label}) WHERE t.key = %s RETURN id(t)::text", (Jsonb(key),)).fetchone()
    return row[0] if row else None


def _key_of(conn, element: str) -> str:
    return conn.execute("MATCH (t) WHERE id(t) = %s::graphid RETURN t.key", (element,)).fetchone()[0]


def _one(conn, subject: str, predicate: str, what: str, required: bool = True) -> dict | None:
    held = claims.held(conn, subject, predicate)
    if len(held) > 1:
        raise Unresolved(f"{what}: {len(held)} held {predicate} claims, expected one")
    if not held and required:
        raise Unresolved(f"{what}: no held {predicate} claim")
    return held[0] if held else None


def resolve(conn, role: str) -> dict:
    """Everything a call for `role` uses, from held claims, with the ids of the claims it was
    resolved from. Parameters layer: the model's, then the role's."""
    role_id = _thing(conn, "model_role", role)
    if role_id is None:
        raise Unresolved(f"no model role {role!r} (a module's manifest declares its roles under `models`)")
    used = []

    def take(subject, predicate, what, required=True):
        c = _one(conn, subject, predicate, what, required)
        if c:
            used.append(c["id"])
        return c

    uses = take(role_id, "models.uses", f"role {role}")
    model = uses["object"]
    model_key = _key_of(conn, model)
    model_id = take(model, "models.model_id", f"model {model_key} (load a catalog naming it)")
    served = take(model, "models.served_by", f"model {model_key}")
    endpoint = served["object"]
    endpoint_key = _key_of(conn, endpoint)
    api = take(endpoint, "models.api", f"endpoint {endpoint_key}")
    base_url = take(endpoint, "models.base_url", f"endpoint {endpoint_key}", required=False)
    key_env = take(endpoint, "models.key_env", f"endpoint {endpoint_key}", required=False)
    prices = take(model, "models.prices", f"model {model_key}", required=False)
    model_params = take(model, "models.params", f"model {model_key}", required=False)
    role_params = take(role_id, "models.params", f"role {role}", required=False)
    budget = take(role_id, "models.budget", f"role {role} (a call needs a budget)")
    params = {**(model_params or {}).get("value", {}), **(role_params or {}).get("value", {})}
    return {"role": role, "role_id": role_id, "model": model_key, "model_id": model_id["value"],
            "endpoint": endpoint_key, "api": api["value"], "base_url": base_url and base_url["value"],
            "key_env": key_env and key_env["value"], "prices": (prices or {}).get("value", {}),
            "params": params, "budget": budget["value"], "budget_since": budget["recorded_at"],
            "resolved_from": used}


def cost_usd(prices: dict, usage: dict) -> float:
    """USD for one call at per-million-token prices. A model without input and output prices
    (a local model, or the Claude Code subscription) costs 0. Missing cache prices are charged
    high: reads at the input price, writes at 2x input (the 1-hour cache write rate), so an
    unknown price never under-counts a budget."""
    if "input" not in prices or "output" not in prices:
        return 0.0
    p_in = prices["input"]
    per_m = (usage.get("input_tokens", 0) * p_in + usage.get("output_tokens", 0) * prices["output"]
             + usage.get("cache_read_input_tokens", 0) * prices.get("cache_read", p_in)
             + usage.get("cache_creation_input_tokens", 0) * prices.get("cache_write", 2 * p_in))
    return round(per_m / 1_000_000, 8)


def tokens(usage: dict) -> int:
    return sum(int(usage.get(f, 0) or 0) for f in TOKEN_FIELDS)


def spent(conn, role: str, since: str) -> dict:
    """What a role's recorded calls have cost since `since` (the held budget's recorded_at)."""
    row = conn.execute(
        f"""SELECT count(*), coalesce(sum((properties->'content'->>'cost_usd')::numeric), 0),
                   coalesce(sum((properties->'content'->>'tokens')::bigint), 0)
            FROM {db.graph_name()}.source
            WHERE properties->>'kind' = 'model_call' AND properties->'content'->>'role' = %s
              AND properties->'content'->>'status' = 'ok' AND properties->>'recorded_at' >= %s""",
        (role, since)).fetchone()
    return {"calls": row[0], "usd": float(row[1]), "tokens": int(row[2])}


def check_budget(conn, cfg: dict) -> dict:
    """The role's spend; raises OverBudget once it has reached any limit of its budget."""
    s = spent(conn, cfg["role"], cfg["budget_since"])
    for unit in ("usd", "tokens"):
        limit = cfg["budget"].get(unit)
        if limit is not None and s[unit] >= limit:
            raise OverBudget(f"model role {cfg['role']} has spent {s[unit]} of its {limit} {unit} budget; "
                             f"refusing the call")
    if not any(cfg["budget"].get(u) is not None for u in ("usd", "tokens")):
        raise OverBudget(f"model role {cfg['role']}: budget {cfg['budget']} names no usd or tokens limit")
    return s


def record(conn, content: dict, causation: str | None, depth: int) -> str:
    """A model call (or a refused or failed one) as a `model_call` source, kept unchanged.
    Committed on its own, so the record stays when the caller then fails."""
    conn.rollback()  # whatever the resolve or budget check left open
    with db.activation(conn, causation, depth):
        return claims.ensure_source(conn, "model_call", json.loads(json.dumps(content)),
                                    f"model:{content.get('model', 'unresolved')}")
