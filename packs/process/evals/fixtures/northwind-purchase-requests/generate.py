"""Generate Northwind's synthetic purchase-request log (log/purchase-requests.csv).

Northwind Foods is the demo's synthetic company (ADR 0026). From a fixed seed, so the file
is the same on every run:
- 120 requests submitted on working days from January to September 2026.
- A line manager reviews each one. A few go back to the requester for revision and are
  reviewed again; a few are rejected.
- Requests over 10,000 euros need the finance controller's approval before procurement
  creates the purchase order. Most urgent ones skip it: the order is created and sent
  first, and the controller approves afterwards (what Maya Chen describes in the
  northwind live scenario).
- Resources are pseudonymous user ids, never names; the adapter drops them anyway.

    uv run python packs/process/evals/fixtures/northwind-purchase-requests/generate.py
"""

from __future__ import annotations

import csv
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 2026
CASES = 120
CATEGORIES = ["ingredients", "packaging", "equipment", "it", "facilities"]
USERS = {
    "requester": [f"u{n:03d}" for n in range(10, 40)],
    "line manager": ["u101", "u102", "u103", "u104"],
    "finance controller": ["u201"],
    "procurement": ["u301", "u302"],
}


def working_day(rng: random.Random, start: datetime, end: datetime) -> datetime:
    while True:
        t = start + timedelta(seconds=rng.randrange(int((end - start).total_seconds())))
        if t.weekday() < 5:
            return t.replace(hour=rng.randint(8, 16), minute=rng.randrange(60), second=0)


def later(rng: random.Random, t: datetime, low: float, high: float) -> datetime:
    return (t + timedelta(days=rng.uniform(low, high))).replace(second=0, microsecond=0)


def generate() -> list[dict[str, str]]:
    rng = random.Random(SEED)
    start = datetime(2026, 1, 2, tzinfo=UTC)
    end = datetime(2026, 9, 25, tzinfo=UTC)
    submitted = sorted(working_day(rng, start, end) for _ in range(CASES))
    rows: list[dict[str, str]] = []
    for n, t in enumerate(submitted, start=1):
        case = f"PR-{1000 + n}"
        big = rng.random() < 0.3
        amount = rng.randrange(10_500, 60_000, 50) if big else rng.randrange(200, 10_000, 50)
        urgent = rng.random() < 0.2
        attrs = {"amount": str(amount), "category": rng.choice(CATEGORIES), "urgent": str(urgent).lower()}
        requester = rng.choice(USERS["requester"])
        manager = rng.choice(USERS["line manager"])

        def event(
            activity: str, at: datetime, role: str, user: str, case: str = case, attrs: dict = attrs
        ) -> None:
            rows.append(
                {
                    "case_id": case,
                    "activity": activity,
                    "timestamp": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "role": role,
                    "resource": user,
                    **attrs,
                }
            )

        event("PR_SUBMIT", t, "requester", requester)
        t = later(rng, t, 0.2, 2.0)
        event("PR_REVIEW", t, "line manager", manager)
        if rng.random() < 0.08:
            t = later(rng, t, 0.5, 3.0)
            event("PR_REVISE", t, "requester", requester)
            t = later(rng, t, 0.2, 1.5)
            event("PR_REVIEW", t, "line manager", manager)
        if rng.random() < 0.06:
            t = later(rng, t, 0.0, 0.2)
            event("PR_REJECT", t, "line manager", manager)
            continue
        buyer = rng.choice(USERS["procurement"])
        if amount > 10_000 and urgent and rng.random() < 0.75:
            # Urgent: the order goes out first, the controller approves afterwards.
            t = later(rng, t, 0.05, 0.3)
            event("PO_CREATE", t, "procurement", buyer)
            t = later(rng, t, 0.02, 0.2)
            event("PO_SEND", t, "procurement", buyer)
            t = later(rng, t, 1.0, 5.0)
            event("PR_APPROVE", t, "finance controller", USERS["finance controller"][0])
            continue
        if amount > 10_000:
            t = later(rng, t, 0.5, 4.0)
            event("PR_APPROVE", t, "finance controller", USERS["finance controller"][0])
        t = later(rng, t, 0.2, 1.5)
        event("PO_CREATE", t, "procurement", buyer)
        t = later(rng, t, 0.05, 1.0)
        event("PO_SEND", t, "procurement", buyer)
    rows.sort(key=lambda r: (r["timestamp"], r["case_id"]))
    return rows


def main() -> None:
    rows = generate()
    out = HERE / "log" / "purchase-requests.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{out}: {len(rows)} events in {len({r['case_id'] for r in rows})} cases")


if __name__ == "__main__":
    main()
