"""M2: each kernel operation is also a tool-shaped script (JSON in, JSON out, schema-checked)."""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from kernel import Kernel

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tool(db_url, tmp_path):
    env = {**os.environ, "DATABASE_URL": db_url, "PC_DATA": str(tmp_path / "data")}

    def call(name, payload, ok=True):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / f"{name}.py")], input=json.dumps(payload),
                           capture_output=True, text=True, env=env, timeout=60)
        assert (r.returncode == 0) == ok, r.stderr
        return json.loads(r.stdout) if ok else r
    return call


def test_put_assert_query_why_through_scripts(db_url, tool, tmp_path):
    k = Kernel.connect(db_url, data_root=tmp_path / "data")
    ns = f"urn:test:{uuid.uuid4().hex[:8]}:"
    k.ensure_node("agent", ns + "me")
    k.ensure_node("thing", ns + "room")
    k.ensure_node("predicate", ns + "temperature")
    ctx = k.create_context("session", "morning readings", iri=ns + "ctx")
    log = tmp_path / "log.txt"
    log.write_text(f"21.5C {ns}\n")

    src = tool("kernel_put_source", {"file": str(log), "media_type": "text/plain"})
    a = tool("kernel_assert", {
        "subject": ns + "room", "predicate": ns + "temperature", "value": 21.5, "context": ns + "ctx",
        "method": "observed", "confidence": 0.9, "asserted_by": ns + "me",
        "evidence": [{"sha256": src["sha256"], "locator": {"line_start": 1, "line_end": 1}, "excerpt": "21.5C"}],
        "args": {"unit": {"value": "C"}}})
    q = tool("kernel_query", {"subject": ns + "room", "context": str(ctx)})
    assert [(r["id"], r["value"], r["args"]) for r in q["assertions"]] == [(a["id"], 21.5, {"unit": "C"})]
    why = tool("kernel_why", {"id": a["id"]})
    assert why["evidence"][0]["source"]["sha256"] == src["sha256"]
    assert why["evidence"][0]["excerpt"] == "21.5C"

    # No evidence for an observed fact: the script fails, nothing is written.
    bad = tool("kernel_assert", {
        "subject": ns + "room", "predicate": ns + "temperature", "value": 30, "context": ns + "ctx",
        "method": "observed", "confidence": 0.9, "asserted_by": ns + "me"}, ok=False)
    assert "EvidenceRequired" in bad.stderr and bad.stdout == ""
    # Input that breaks the schema is refused before touching the kernel.
    bad = tool("kernel_assert", {"subject": "x"}, ok=False)
    assert "ValidationError" in bad.stderr
    k.conn.close()
