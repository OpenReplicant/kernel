"""Module library: what every worker module shares. Depends on psycopg only.

  conn = connect("kkm_example")                 # DATABASE_URL, then SET ROLE to the module's own role
  listen(conn, step)                            # step(conn) on start, on each notification, and every sweep
  listen(conn, step, patterns=manifest_patterns("conformance"))   # ... only when an applied claim fits (D-79)
  with run(cur, MODULE) as (run_id, outputs):   # a recorded run; failed if the block raises
      submit(cur, change_set, run_id)           # the only way a module writes (api.submit_change_set)
  ingest_filter(repo, paths)                    # encoders: the clean paths, before anything is stored

The kernel enforces everything (roles, scopes, lanes); this library only removes the boilerplate.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

CHANNEL = "kernel_change_set"
# The vocabulary packs' prefixes, for trigger patterns (scripts/catalog.py --check keeps this in step).
PREFIXES = {
    "bbo": "https://www.irit.fr/recherches/MELODI/ontologies/BBO#", "cito": "http://purl.org/spar/cito/",
    "dcterms": "http://purl.org/dc/terms/", "dqv": "http://www.w3.org/ns/dqv#", "fabio": "http://purl.org/spar/fabio/",
    "kk": "https://w3id.org/knowledge-kernel/ns#", "kkp": "https://w3id.org/knowledge-kernel/profile#",
    "kkt": "https://w3id.org/knowledge-kernel/tosca#", "oa": "http://www.w3.org/ns/oa#",
    "owl": "http://www.w3.org/2002/07/owl#", "pplan": "http://purl.org/net/p-plan#", "prov": "http://www.w3.org/ns/prov#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "schema": "http://schema.org/", "skos": "http://www.w3.org/2004/02/skos/core#", "sosa": "http://www.w3.org/ns/sosa/",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}


class Pattern:
    """A trigger pattern (D-79): `S P O`, each a CURIE, an IRI, `?` (anything) or a prefix ending in `*`.
    It fits a claim whose subject, predicate and object (entity, or literal value) match."""

    def __init__(self, spec: str):
        parts = spec.split()
        if len(parts) != 3:
            raise ValueError(f"a trigger pattern is 'S P O': {spec!r}")
        self.spec, self.terms = spec, [self.expand(t) for t in parts]

    @staticmethod
    def expand(term: str) -> str:
        if term == "?":
            return term
        prefix, _, local = term.partition(":")
        return PREFIXES[prefix] + local if prefix in PREFIXES and not local.startswith("//") else term

    def where(self) -> tuple[str, list]:
        """A SQL condition over api.claims c, and its arguments."""
        conds, args = [], []
        for column, term in zip(("c.subject", "c.predicate", "coalesce(c.object_entity, c.object_value->>'@value')"),
                                self.terms):
            if term == "?":
                continue
            if term.endswith("*"):
                conds.append(f"{column} like %s")
                args.append(term[:-1].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
            else:
                conds.append(f"{column} = %s")
                args.append(term)
        return " and ".join(conds) or "true", args


def manifest_patterns(module: str) -> list[Pattern]:
    """The `pattern` triggers in modules/<module>/module.yaml under KK_ROOT (or this checkout)."""
    import yaml
    root = Path(os.environ.get("KK_ROOT") or Path(__file__).resolve().parents[1])
    m = yaml.safe_load((root / "modules" / module / "module.yaml").read_text())
    return [Pattern(t["spec"]) for t in m.get("triggers", []) if t["type"] == "pattern"]


def fits(conn: psycopg.Connection, change_set: str, patterns: list[Pattern]) -> bool:
    """Whether the change set added a claim that fits one of the patterns."""
    wheres = [p.where() for p in patterns]
    cond = " or ".join(f"({w})" for w, _ in wheres)
    args = [a for _, xs in wheres for a in xs]
    return conn.execute(f"select exists (select 1 from api.claims c where c.change_set = %s and ({cond}))",
                        [change_set, *args]).fetchone()[0]


def connect(role: Optional[str] = None, url: Optional[str] = None) -> psycopg.Connection:
    """An autocommit connection from DATABASE_URL; SET ROLE to `role` when given (needs a login that
    may switch to it, e.g. kk_authenticator; without a role the login acts as itself)."""
    conn = psycopg.connect(url or os.environ["DATABASE_URL"], autocommit=True)
    if role:
        conn.execute(sql.SQL("set role {}").format(sql.Identifier(role)))
    return conn


def listen(conn: psycopg.Connection, step: Callable[[psycopg.Connection], object],
           wake: Optional[Callable[[str], bool]] = None, sweep: Optional[float] = None,
           channel: str = CHANNEL, patterns: Optional[list[Pattern]] = None) -> None:
    """Run step(conn) now, then again whenever a notification on `channel` passes `wake` (any, by
    default), and at least every `sweep` seconds (KK_SWEEP_SECONDS, default 30): NOTIFY is not durable.
    With `patterns` (D-79), a notification wakes it only when the change set was applied and added a
    claim that fits one of them."""
    sweep = sweep if sweep is not None else float(os.environ.get("KK_SWEEP_SECONDS", "30"))
    conn.execute(sql.SQL("listen {}").format(sql.Identifier(channel)))

    def woken(payload: str) -> bool:
        if patterns:
            note = json.loads(payload)
            if note.get("status") != "applied" or not fits(conn, note["id"], patterns):
                return False
        return wake is None or wake(payload)
    while True:
        step(conn)
        deadline = time.monotonic() + sweep
        while (left := deadline - time.monotonic()) > 0:
            # One notification at a time, the generator closed before woken() queries the connection.
            got = list(conn.notifies(timeout=left, stop_after=1))
            if not got or woken(got[0].payload):
                break


@contextmanager
def run(cur, module: str, params: Optional[dict] = None, plan_step: Optional[str] = None,
        used: Optional[list] = None) -> Iterator[tuple[str, dict]]:
    """A run of `module` (prov:Activity), optionally carrying out `plan_step` (p-plan:correspondsToStep)
    and using `used` (prov:used: claim ids, artifacts, entities). Fill `outputs`; the run finishes
    succeeded, or failed if the block raises."""
    cur.execute("select api.start_run(%s, %s, %s, %s)", (module, plan_step, Jsonb(used or []), Jsonb(params or {})))
    run_id = str(cur.fetchone()[0])
    outputs: dict = {}
    try:
        yield run_id, outputs
    except Exception:
        cur.execute("select api.finish_run(%s, 'failed', %s)", (run_id, Jsonb(outputs)))
        raise
    cur.execute("select api.finish_run(%s, 'succeeded', %s)", (run_id, Jsonb(outputs)))


def submit(cur, change_set: dict, run_id: str) -> dict:
    """Submit one change set generated by `run_id`; returns the kernel's answer (id, status, ...)."""
    cur.execute("select api.submit_change_set(%s)", (Jsonb({**change_set, "generated_by": run_id}),))
    return cur.fetchone()[0]


def ingest_filter(repo: Path, paths: list[str]) -> dict:
    """Run the ingest filter (KK_INGEST_FILTER, default `python -m ingest_filter`) on `paths` relative
    to `repo`: {"paths": clean, "blocked": findings}. Raises when the filter cannot run (fail closed)."""
    cmd = shlex.split(os.environ.get("KK_INGEST_FILTER") or f"{shlex.quote(sys.executable)} -m ingest_filter")
    mb = sum((repo / p).stat().st_size for p in paths if (repo / p).is_file()) / (1 << 20)
    limit = 300 + max(300, mb * float(os.environ.get("KK_FILTER_SECONDS_PER_MB", "2")))  # the filter's own limit, and time to copy
    proc = subprocess.run(cmd + ["--repo", str(repo)], input=json.dumps(paths), capture_output=True, text=True,
                          timeout=limit)
    if proc.returncode != 0:
        raise RuntimeError(f"ingest filter failed (fail closed): {proc.stderr.strip()[-400:]}")
    return json.loads(proc.stdout)
