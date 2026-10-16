"""T19: export and import round trip, run by scripts/smoke_kernel.sh after the other checks.

Exports the smoke database (every claim the checks made, the packs and the self-model), imports it into
a fresh kernel without packs, exports that, and compares: every claim with its agent, scope, evidence,
validity, qualifiers, references, derivations and current status (replacements by content), and every
artifact. A second import changes nothing. Env: KK_SMOKE_DB, PG* (a superuser), PYTHON's kk installed.
"""
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import psycopg
from rdflib import Dataset

from kk.transfer import read

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def kk(db, *args):
    out = subprocess.run([sys.executable, "-m", "kk", *args], capture_output=True, text=True,
                         env={**os.environ, "KK_ADMIN_URL": f"dbname={db}"})
    if out.returncode:
        raise SystemExit(f"kk {' '.join(args)} failed: {out.stderr[-1500:]}")
    return out.stdout


def count(db) -> int:
    with psycopg.connect(f"dbname={db}") as conn:
        return conn.execute("select count(*) from kernel.claim").fetchone()[0]


def records(path: Path) -> tuple[Counter, set]:
    ds = Dataset()
    ds.parse(path, format="nquads")
    _, _, claims, artifacts = read(ds)
    key = {c["old"]: json.dumps([c["op"]["subject"], c["op"]["predicate"], c["op"]["object"], c["agent"], c["scope"]],
                                sort_keys=True) for c in claims}
    out = Counter()
    for c in claims:
        op = {k: v for k, v in c["op"].items() if k != "ref"}
        op["derived_from"] = sorted(key.get(d, d) for d in op.get("derived_from", []))
        out[json.dumps([op, c["agent"], c["scope"], c["status"], c["reason"], key.get(c["replaced_by"])],
                       sort_keys=True, default=str)] += 1
    return out, {json.dumps(a, sort_keys=True) for a in artifacts.values()}


def main() -> int:
    src, dst = os.environ.get("KK_SMOKE_DB", "kk_smoke"), os.environ.get("KK_SMOKE_DB", "kk_smoke") + "_copy"
    with tempfile.TemporaryDirectory() as tmp:
        first, second = Path(tmp) / "a.nq", Path(tmp) / "b.nq"
        kk(src, "export", "--out", str(first))
        subprocess.run(["dropdb", "--if-exists", dst], check=True)
        subprocess.run(["createdb", dst], check=True)
        kk(dst, "init", "--no-packs")
        done = json.loads(kk(dst, "import", str(first)))
        before = count(dst)
        kk(dst, "import", str(first))
        kk(dst, "export", "--out", str(second))
        a, arts_a = records(first)
        b, arts_b = records(second)
        check("export: every claim with provenance as N-Quads", sum(a.values()) > 500,
              f"{sum(a.values())} claims, {first.stat().st_size // 1024} KiB")
        check("import keeps every claim with its agent, scope, evidence, references, derivations and status",
              a == b, f"{done}; missing {sum((a - b).values())}, extra {sum((b - a).values())}")
        for line in list((a - b))[:2]:
            print("   missing:", line[:300])
        check("import keeps every artifact", arts_a == arts_b, f"{len(arts_a)} artifacts")
        check("statuses survive (deprecated, superseded with a replacement)",
              any('"deprecated", "superseded"' in k and not k.endswith("null]") for k in b), "")
        check("importing the same file again changes nothing", count(dst) == before, f"{before} claims")
        subprocess.run(["dropdb", "--if-exists", dst], check=True)
    print("T19 export/import", "passed" if all(checks) else "FAILED", f"({sum(checks)}/{len(checks)})")
    return 0 if all(checks) else 1


sys.exit(main())
