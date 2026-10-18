"""M5-T4 unit checks on the scripted interview (modules/skill-interview/tests/fixtures/acme.yaml): after
each answer the gaps are the ones the script expects, every gap an answer was asked about is closed by
it, and none are left at the end; process steps from event logs and BPMN files raise no questions."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/map-gaps"), str(ROOT / "modules/skill-interview/tests")]
from map_gaps.gaps import KIND, gaps  # noqa: E402
from scripted import load  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


def rows(ops, start, evidence=None):
    return [{"id": str(start + i), "subject": op["subject"], "predicate": op["predicate"],
             "object_entity": op["object"].get("entity"), "object_value": op["object"].get("value"),
             "evidence_type": evidence or op["evidence_type"]} for i, op in enumerate(ops)]


_, _, answers = load()
claims, before, counts = [], set(), []
for n, a in enumerate(answers):
    claims += rows(a["ops"], 1000 * n)
    found = gaps(claims)
    now = {(g["kind"], g["target"]) for g in found}
    counts.append(len(now))
    check(now == a["open"], f"after message {a['id']}: the gaps are {sorted(k.rsplit(':', 1)[1] + ' ' + t.rsplit(':', 1)[1] for k, t in now)}")
    check(a["closes"] <= before and not (a["closes"] & now), f"message {a['id']} closes the gaps it was asked about")
    check(all(g["premises"] and g["text"].endswith("?") for g in found), f"after message {a['id']}: each gap is a question derived from claims")
    before = now
check(counts[-1] == 0 and counts[1:] == sorted(counts[1:], reverse=True), f"the gaps shrink as the person answers: {counts}")

q = {g["target"].rsplit(":", 1)[1]: g["text"] for g in gaps(rows(answers[1]["ops"], 0))}
check(q["raise-a-purchase-order"] == "What happens after “Raise a purchase order”, or does the process end there?",
      "a question names the step by its label")
process = [op for op in answers[1]["ops"] if "BBO" in op["predicate"] or op["subject"].startswith("urn:kk:process:")]
check(gaps(rows(process, 0)) and gaps(rows(process, 0, "deterministic")) == [],
      "steps from a BPMN file or an event log raise no questions")
check(all(g["kind"].startswith(KIND) and g["iri"].startswith("urn:kk:gap:") for g in gaps(rows(answers[1]["ops"], 0))),
      "gap IRIs and kinds")
print(f"{ok} checks passed")
