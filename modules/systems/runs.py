"""Runs and the Record joint (docs/ARCHITECTURE.md): a run is a session context in the kernel
plus a run.run row; when it ends, its trace becomes a source the kernel can cite.

- start_run(kernel, system, budget_usd, by, conditions): the session context and the row.
- record_run(kernel, run_id, by): writes runs/<id>/trace.jsonl under the data root, stores it
  as a source produced by `by` (as recorder), marks the run finished, and returns where each
  event landed so observed facts can cite its lines.
"""
import json
import uuid

from psycopg.types.json import Jsonb

from pclib import connect


def run_context_iri(run_id) -> str:
    return f"urn:pc:run:{run_id}"


def start_run(kernel, system, budget_usd: float, by, conditions: dict | None = None,
              toggles: dict | None = None) -> str:
    run_id = uuid.uuid4()
    ctx = kernel.create_context("session", f"run {run_id}", iri=run_context_iri(run_id),
                                conditions={"system": kernel.iri(system) if isinstance(system, str) else str(system),
                                            "budget_usd": budget_usd, **(conditions or {})})
    with connect(autocommit=True) as conn:
        conn.execute("insert into run.run (id, context, system, toggles, budget_usd, artifact_dir)"
                     " values (%s, %s, %s, %s, %s, %s)",
                     (run_id, ctx, kernel.node_id(system, "context"), Jsonb(toggles or {}), budget_usd,
                      f"runs/{run_id}"))
    return str(run_id)


def record_run(kernel, run_id: str, by, status: str = "done") -> dict:
    """Store the run's trace as a source. Returns {"sha256", "context", "events": [{line,
    event, slot, payload}]} with 1-based line numbers in trace.jsonl."""
    with connect(autocommit=True) as conn:
        ctx, artifact_dir = conn.execute("select context, artifact_dir from run.run where id = %s",
                                         (run_id,)).fetchone()
        rows = conn.execute("select id, at, task_id, episode, step_key, event, slot, port, payload"
                            " from run.trace where run_id = %s order by id", (run_id,)).fetchall()
        conn.execute("update run.run set status = %s, finished_at = coalesce(finished_at, now())"
                     " where id = %s", (status, run_id))
    path = kernel.data_root / (artifact_dir or f"runs/{run_id}") / "trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    events = []
    with path.open("w") as f:
        for line, (seq, at, task, ep, key, event, slot, port, payload) in enumerate(rows, 1):
            f.write(json.dumps({"seq": seq, "at": at.isoformat(), "task_id": task, "episode": ep,
                                "step_key": key, "event": event, "slot": slot, "port": port,
                                "payload": payload}, sort_keys=True) + "\n")
            events.append({"line": line, "event": event, "slot": slot, "payload": payload or {}})
    sha = kernel.put_source(path, "application/x-ndjson", uri=f"{run_context_iri(run_id)}/trace.jsonl")
    src = kernel.source_node(sha)
    if not kernel.query(subject=src, predicate="k:produced_by"):
        kernel.assert_(src, "k:produced_by", kernel.node_id(by, "agent"), context=ctx, method="defaulted",
                       confidence=1, asserted_by=by, args={"as": "recorder"})
    return {"sha256": sha, "context": ctx, "events": events}
