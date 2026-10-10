"""M0 checks against a running RuleGo-Server (rulego/Containerfile) with this repo at /repo.
Skipped unless RULEGO_URL is set. The pclib gate also needs RULEGO_DATABASE_URL: the database
the container's steps write to (with db/*.sql applied)."""
import os
import uuid
from pathlib import Path

import psycopg
import pytest

from pclib.rulego import ChainFailed, RuleGo

ROOT = Path(__file__).resolve().parents[2]
URL = os.environ.get("RULEGO_URL")
DB = os.environ.get("RULEGO_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="RULEGO_URL not set")
TRAIL = ["inc", "judge", "odd", "double", "inc", "judge", "even", "double", "inc", "judge", "odd", "double", "final"]


@pytest.fixture(scope="module")
def rg():
    r = RuleGo(URL)
    r.deploy_paths(ROOT / "rulego/spike/exec/chains", ROOT / "rulego/spike/pclib/chains")
    return r


def test_exec_gate_sequence_loop_branch_subchain_sync_result(rg):
    out = rg.execute("m0_main", {"run_id": "r", "task_id": "t", "episode": 0, "data": {}}, timeout=60)
    assert out["data"]["count"] == 3 and out["data"]["doubled"] == 6 and out["data"]["trail"] == TRAIL


def test_exec_self_recursion_through_flow(rg):
    assert rg.execute("m0_recurse", {"data": {"n": 25}}, timeout=60)["data"]["count"] == 25


def test_exec_limits(rg):
    with pytest.raises(ChainFailed) as e:
        rg.execute("m0_probe", {"data": {"exit": 75}}, timeout=60)
    assert e.value.status == 400 and e.value.retryable
    with pytest.raises(ChainFailed, match="argument list too long"):
        rg.execute("m0_probe", {"data": {"big": "x" * 140_000}}, timeout=60)


@pytest.mark.skipif(not DB, reason="RULEGO_DATABASE_URL not set")
def test_pclib_gate_with_replay(rg):
    with psycopg.connect(DB, autocommit=True) as conn:
        ctx, rid = uuid.uuid4(), uuid.uuid4()
        conn.execute("insert into kb.node (id, kind, iri) values (%s, 'context', %s)", (ctx, f"urn:smoke:{ctx}"))
        conn.execute("insert into run.run (id, context, system, budget_usd) values (%s, %s, %s, 1)", (rid, ctx, ctx))
        meta = {"run_id": str(rid), "task_id": "T1", "episode": 1}
        first = rg.execute("pc_main", {}, meta, timeout=60)
        second = rg.execute("pc_main", {}, meta, timeout=60)
        assert first == second and first["trail"] == TRAIL
        steps = conn.execute("select count(*) from run.step where run_id = %s", (rid,)).fetchone()[0]
        assert steps == 13                                   # replayed, not re-run
