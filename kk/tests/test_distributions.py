"""Distributions are found where KK_ROOT says (D-131): the kernel's image installs kk into site-packages and keeps
distributions/ at KK_ROOT, so `kk distribution list` and `kk demo --distribution NAME` must read them from there.
Without KK_ROOT they are the checkout's own. Needs PyYAML only.

    python kk/tests/test_distributions.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, "" if ok else detail)


def names(env):
    code = "import kk.distributions as d; print(','.join(d.names()))"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return out.stdout.strip().split(",") if out.stdout.strip() else []


with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / "distributions/only-here").mkdir(parents=True)
    (Path(tmp) / "distributions/only-here/distribution.yaml").write_text("name: only-here\n")
    env = {k: v for k, v in os.environ.items() if k != "KK_ROOT"}
    own = names(env)
    check("without KK_ROOT, the checkout's distributions", "business" in own and "only-here" not in own, own)
    there = names({**env, "KK_ROOT": tmp})
    check("with KK_ROOT, the distributions under it", there == ["only-here"], there)

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
