"""RCAEval on the kernel's Analyse step (M3-T8): does telemetry aggregated per window, as the
observer records it, keep enough signal to find the root cause?

  python3.12 eval/rcaeval/run.py --dataset re1-ob --method kernel --window 60
  python3.12 eval/rcaeval/run.py --dataset re1-ob --method baro          # a published baseline, for reference

Runs RCAEval's own main.py (data download, the fault injection time, Avg@5 scoring) unchanged; the
`kernel` method stands in for its `dummy` slot. The method: each metric averaged over windows of
--window seconds (the observer's KK_WINDOW_SECONDS, D-52), then each metric's largest robust z-score
after the injection against the windows before it (median and IQR, as BARO), metrics ranked by it.
RCAEval (MIT, Pham et al., WWW 2025) is checked out at RCAEVAL_DIR; see README.md. Results go to
eval/rcaeval/runs/<date>-<dataset>-<method>.json.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import runpy
import shutil
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path(os.environ.get("RCAEVAL_DIR") or Path.home() / ".cache/knowledge-kernel/eval/rcaeval/src")


def windowed(window: int):
    """The kernel's Analyse view of a case: window means, robust z after the injection."""
    import numpy as np

    sys.path.insert(0, str(HERE))
    from packs import robust_z

    from RCAEval.io.time_series import preprocess

    def method(data, inject_time=None, dataset=None, **kwargs):
        data = data.copy()
        data["window"] = (data["time"] - inject_time) // window   # windows aligned to the injection
        means = data.groupby("window").mean(numeric_only=True)
        normal = preprocess(data=means[means.index < 0].reset_index(drop=True), dataset=dataset)
        anomal = preprocess(data=means[means.index >= 0].reset_index(drop=True), dataset=dataset)
        cols = [c for c in normal.columns if c in anomal.columns]
        ranks = []
        for col in cols:
            a, b = normal[col].to_numpy(), anomal[col].to_numpy()
            if len(a) < 2 or len(b) < 1:
                continue
            z = robust_z(a, b)
            ranks.append((col, float(np.nanmax(z)) if np.isfinite(z).any() else 0.0))
        ranks.sort(key=lambda x: x[1], reverse=True)
        return {"node_names": cols, "ranks": [c for c, _ in ranks]}
    return method


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--method", default="kernel")
    ap.add_argument("--window", type=int, default=60)
    a = ap.parse_args()
    os.chdir(SRC)
    sys.path.insert(0, str(SRC))
    # RCAEval's main.py writes each case's result to output/results and scores every file there: clear
    # it, or one run's results are scored again with the next (D-65).
    shutil.rmtree(SRC / "output" / "results", ignore_errors=True)
    method = a.method
    if a.method == "kernel":
        import RCAEval.e2e as e2e
        e2e.dummy = windowed(a.window)
        method = "dummy"
    sys.argv = ["main.py", "--method", method, "--dataset", a.dataset, "--report-chance"]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        runpy.run_path(str(SRC / "main.py"), run_name="__main__")
    text = out.getvalue()
    scores = {m.group(1): float(m.group(2)) for m in re.finditer(r"^Avg@5-(\w+):\s+([\d.]+)", text, re.M)}
    chance = {m.group(1): float(m.group(2)) for m in re.finditer(r"^Chance@5-(\w+):\s+([\d.]+)", text, re.M)}
    if not scores:
        print(text)
        return 1
    name = a.method + (f"-w{a.window}" if a.method == "kernel" else "")
    result = {"benchmark": "RCAEval", "dataset": a.dataset, "method": name, "avg@5": scores,
              "avg@5_mean": round(sum(scores.values()) / len(scores), 4), "chance@5": chance,
              "rcaeval_commit": os.popen(f"git -C {SRC} rev-parse HEAD").read().strip()}
    (HERE / "runs").mkdir(exist_ok=True)
    (HERE / "runs" / f"{date.today().isoformat()}-{a.dataset}-{name}.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
