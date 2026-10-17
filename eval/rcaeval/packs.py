"""Evidence packs for RCAEval RE2/RE3 cases (M3-T8 step 2): what the kernel's observer and anomaly
modules would record about a fault, as numbered observations an agent can cite.

Each observation is one signal of one service, aggregated per window (D-52) before and after the
fault injection: metrics (simple_metrics.csv), log lines per service (logts.csv), log lines at error or
warning level (logs.csv), trace errors and latency per operation (tracets_err.csv, tracets_lat.csv).
Its score is the largest robust z-score (median, IQR) of the windows after the injection against the
windows before it, as in eval/rcaeval/run.py. The pack keeps the strongest observations.

  python eval/rcaeval/packs.py show CASE_DIR                       one case's pack and prompt
  python eval/rcaeval/packs.py build SUITE_DIR > prompts.jsonl      every case's prompt (for a model)
  python eval/rcaeval/packs.py score SUITE_DIR [answers.jsonl]      Avg@5: services ranked by their
                                    strongest observation (no model), and by the model's answers if given
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

WINDOW = 60          # seconds, the observer's window
SPAN = 600           # seconds before and after the injection, as RCAEval's default (length 20 minutes)
SERVICE = re.compile(r"^([a-z]+(?:-[a-z]+)*?)(?:service)?[_-]")


def service(column: str) -> str:
    """The service a column belongs to: `cartservice_cpu`, `cartservice_1`, `cartservice_AddItem` → cartservice."""
    name = column.split("_")[0]
    return {"frontendservice": "frontend"}.get(name, name).replace("-db", "")


def robust_z(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    """z-scores of `after` against `before`: median and IQR, falling back to the standard deviation, then
    a tenth of the median, when the baseline barely varies. A scale of 1 (sklearn's RobustScaler on a flat
    baseline) would score a byte counter that was 0 in raw bytes and drown every other signal (D-65)."""
    med = np.median(before)
    scale = np.subtract(*np.percentile(before, [75, 25])) or np.std(before) or abs(med) * 0.1 or 1.0
    return (after - med) / scale


def zscores(frame: pd.DataFrame, inject: int, kind: str) -> list[dict]:
    """One observation per column: window means before and after, and the largest robust z after."""
    frame = frame[(frame["time"] >= inject - SPAN) & (frame["time"] < inject + SPAN)].copy()
    frame["window"] = (frame["time"] - inject) // WINDOW
    means = frame.drop(columns="time").groupby("window").mean(numeric_only=True)
    before, after = means[means.index < 0], means[means.index >= 0]
    out = []
    for col in means.columns:
        a, b = before[col].dropna().to_numpy(), after[col].dropna().to_numpy()
        if len(a) < 2 or len(b) < 1 or np.all(a == a[0]) and np.all(b == a[0]):
            continue
        z = robust_z(a, b)
        med = np.median(a)
        i = int(np.nanargmax(np.abs(z)))
        out.append({"service": service(col), "signal": f"{kind} {col}", "before": round(float(med), 3),
                    "after": round(float(b[i]), 3), "z": round(float(z[i]), 1)})
    return out


def error_logs(path: Path, inject: int) -> list[dict]:
    """Error and warning log lines per service, per window, as a time series."""
    logs = pd.read_csv(path, usecols=["timestamp", "container_name", "level", "message"])
    logs["time"] = logs["timestamp"] // 1_000_000_000
    bad = logs[logs["level"].astype(str).str.lower().isin(["error", "warning", "warn", "fatal", "critical"])
               | logs["message"].astype(str).str.contains(r"error|exception|fail|timeout|refused", case=False)]
    if bad.empty:
        return []
    counts = bad.groupby([bad["time"], "container_name"]).size().unstack(fill_value=0)
    full = pd.DataFrame(index=range(inject - SPAN, inject + SPAN)).join(counts).fillna(0)
    full.index.name = "time"
    obs = zscores(full.reset_index(), inject, "error log lines")
    samples = {}
    for svc, group in bad[bad["time"] >= inject].groupby("container_name"):
        samples[svc] = group["message"].astype(str).str.slice(0, 160).value_counts().index[:2].tolist()
    for o in obs:
        o["examples"] = samples.get(o["signal"].split(" ")[-1], [])
    return obs


def pack(case: Path, top: int = 40) -> dict:
    inject = int((case / "inject_time.txt").read_text().split()[0])
    obs = []
    for name, kind in (("simple_metrics.csv", "metric"), ("logts.csv", "log lines"),
                       ("tracets_err.csv", "trace errors"), ("tracets_lat.csv", "trace latency")):
        if (case / name).exists():
            obs += zscores(pd.read_csv(case / name), inject, kind)
    if (case / "logs.csv").exists():
        obs += error_logs(case / "logs.csv", inject)
    obs = [o for o in obs if np.isfinite(o["z"])]
    obs.sort(key=lambda o: -abs(o["z"]))
    services = sorted({o["service"] for o in obs})
    kept = obs[:top]
    for i, o in enumerate(kept, 1):
        o["id"] = f"O{i}"
    truth = case.parent.name.rsplit("_", 1)[0]
    return {"case": f"{case.parent.parent.name}/{case.parent.name}/{case.name}", "truth": truth,
            "fault": case.parent.name.rsplit("_", 1)[1], "services": services, "observations": kept,
            "max_z": {s: max(abs(o["z"]) for o in obs if o["service"] == s) for s in services}}


def text(p: dict) -> str:
    lines = [f"{o['id']} {o['service']}: {o['signal']} {o['before']} -> {o['after']} (z {o['z']})"
             + (f"; e.g. {' | '.join(o['examples'])}" if o.get("examples") else "") for o in p["observations"]]
    return "\n".join(lines)


ASK = """A fault was injected into one service of a microservice application at time 0. Below are the
strongest anomalies the monitoring recorded, each a signal of one service, aggregated per minute:
its typical value in the 10 minutes before -> its most extreme value after, and a robust z-score.
Anomalies spread from the faulty service to the services that call it, so the loudest signal is not
always the cause. Services: {services}

{observations}

Rank the five services most likely to be the root cause, most likely first, and cite the observations
that decide it. Answer with JSON only: {{"ranks": ["service", ...], "evidence": ["O1", ...]}}"""


def prompt(p: dict) -> str:
    return ASK.format(services=", ".join(p["services"]), observations=text(p))


def cases(suite: Path) -> list[Path]:
    return sorted(p.parent for p in suite.glob("*/*/inject_time.txt") if p.parent.name.isdigit())


def avg5(ranked: list[list[str]], truths: list[str]) -> float:
    """RCAEval's Avg@5: the mean of AC@1 … AC@5."""
    return round(sum(sum(t in r[:k] for r, t in zip(ranked, truths)) / len(truths) for k in range(1, 6)) / 5, 4)


def score(suite: Path, answers: Path | None) -> dict:
    packs = [pack(c) for c in cases(suite)]
    truths = [p["truth"] for p in packs]
    out = {"suite": suite.name, "cases": len(packs),
           "avg@5": {"max z (no model)": avg5([sorted(p["max_z"], key=lambda s: -p["max_z"][s]) for p in packs], truths)}}
    if answers:
        got = {}
        for line in answers.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                a = r["answer"] if isinstance(r["answer"], dict) else json.loads(re.search(r"\{.*\}", r["answer"], re.S).group(0))
                got[r["key"]] = [service(x + "_") if "_" not in x else service(x) for x in a.get("ranks", [])]
        ranked = [got.get(p["case"], []) for p in packs]
        out["avg@5"]["model over the observations"] = avg5(ranked, truths)
        out["answered"] = sum(1 for p in packs if p["case"] in got)
        out["by_fault"] = {}
        for fault in sorted({p["fault"] for p in packs}):
            idx = [i for i, p in enumerate(packs) if p["fault"] == fault]
            out["by_fault"][fault] = {"max z": avg5([sorted(packs[i]["max_z"], key=lambda s: -packs[i]["max_z"][s]) for i in idx], [truths[i] for i in idx]),
                                      "model": avg5([ranked[i] for i in idx], [truths[i] for i in idx])}
    return out


if __name__ == "__main__":
    cmd, target = sys.argv[1], Path(sys.argv[2])
    if cmd == "show":
        p = pack(target)
        print(prompt(p))
        print(json.dumps({"truth": p["truth"], "by max z": sorted(p["max_z"], key=lambda s: -p["max_z"][s])[:5]}))
    elif cmd == "build":
        for c in cases(target):
            p = pack(c)
            print(json.dumps({"key": p["case"], "prompt": prompt(p)}))
    elif cmd == "score":
        print(json.dumps(score(target, Path(sys.argv[3]) if len(sys.argv) > 3 else None), indent=2))
