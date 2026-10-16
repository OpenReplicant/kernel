"""Writes order-to-cash.csv: a small synthetic event log for the BPM demo (M2, D-47 e), deterministic.

The documented process (order-to-cash.bpmn): Receive order → Check credit → (approved) Ship goods →
Send invoice → Receive payment, or (rejected) Reject order. Planted discrepancies, which the
conformance processor must find:
  skip      4 cases skip Check credit (Receive order → Ship goods)
  swap      3 cases send the invoice before shipping (Check credit → Send invoice → Ship goods)
  rework    2 cases check credit twice (Check credit → Check credit)
  slow      Ship goods → Send invoice waits about 5 days in every week-3 case (otherwise about 1 day)
"""
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HAPPY = ["Receive order", "Check credit", "Ship goods", "Send invoice", "Receive payment"]
VARIANTS = (
    [("happy", HAPPY)] * 26
    + [("rejected", ["Receive order", "Check credit", "Reject order"])] * 5
    + [("skip", ["Receive order", "Ship goods", "Send invoice", "Receive payment"])] * 4
    + [("swap", ["Receive order", "Check credit", "Send invoice", "Ship goods", "Receive payment"])] * 3
    + [("rework", ["Receive order", "Check credit", "Check credit", "Ship goods", "Send invoice", "Receive payment"])] * 2
)
RESOURCE = {"Receive order": "sales-1", "Check credit": "credit-1", "Reject order": "credit-1",
            "Ship goods": "warehouse-1", "Send invoice": "billing-1", "Receive payment": "billing-1"}
HOURS = {"Check credit": 4, "Ship goods": 20, "Send invoice": 24, "Receive payment": 72, "Reject order": 2}


def main(out: Path) -> None:
    start = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)          # a Monday; cases over three weeks
    order = [0, 26, 31, 35, 38, 1, 27, 32, 36, 2, 28, 33, 37, 3, 29, 34, 39, 4, 30] + list(range(5, 26))
    rows = []
    for n, i in enumerate(order):
        kind, steps = VARIANTS[i]
        t = start + timedelta(hours=12 * n)
        week3 = t >= start + timedelta(days=14)
        for k, act in enumerate(steps):
            if k:
                h = HOURS[act] + (n % 3)
                if act == "Send invoice" and steps[k - 1] == "Ship goods" and week3:
                    h = 120 + (n % 3)
                t += timedelta(hours=h)
            rows.append({"case_id": f"O-{1001 + n}", "activity": act,
                         "timestamp": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "resource": RESOURCE[act]})
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["case_id", "activity", "timestamp", "resource"])
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("order-to-cash.csv"))
