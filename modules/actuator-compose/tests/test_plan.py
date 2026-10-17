"""M2-T6 unit checks: the actuator carries out only approved steps on its fixed list and allow list."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/actuator-compose")]
from actuator_compose.plan import DCT_TYPE, PPLAN_STEP_OF, RDF_TYPE, SOSA, refusal, steps  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


def plan(step, action, service, n, cs="cs1"):
    rows = [(PPLAN_STEP_OF, {"entity": "urn:kk:plan:p"}), (RDF_TYPE, {"entity": SOSA + "Actuation"}),
            (DCT_TYPE, {"entity": action}), (SOSA + "hasFeatureOfInterest", {"entity": f"urn:kk:node:{service}"}),
            (SOSA + "hasSimpleResult", {"value": {"@value": n}})]
    return [{"id": f"{step}-{i}", "subject": step, "predicate": p, "object_entity": o.get("entity"),
             "object_value": o.get("value"), "change_set": cs} for i, (p, o) in enumerate(rows)]


approved = {"cs1": {"lane": "review", "decided_by": "person_owner", "submitted_by": "agent_planner", "status": "applied"}}
s = steps(plan("urn:kk:plan:p:step:1", "urn:kk:action:scale", "shop", 3), approved)
check(len(s) == 1 and s[0]["action"] == "scale" and s[0]["service"] == "shop" and s[0]["replicas"] == 3
      and s[0]["approved"] and len(s[0]["claims"]) == 5, "an approved scale step is read with its claims")
check(refusal(s[0], {"shop"}, 5) is None, "allowed: scale shop to 3")
check("allow list" in refusal(s[0], {"web"}, 5), "refused: a service not on the allow list")
check("1 to 2" in refusal(s[0], {"shop"}, 2), "refused: more replicas than the maximum")
for name, sets in [("auto lane", {"cs1": {**approved["cs1"], "lane": "auto"}}),
                   ("not decided", {"cs1": {**approved["cs1"], "decided_by": None}}),
                   ("decided by its submitter", {"cs1": {**approved["cs1"], "decided_by": "agent_planner"}})]:
    t = steps(plan("urn:kk:plan:p:step:1", "urn:kk:action:scale", "shop", 3), sets)[0]
    check(not t["approved"] and refusal(t, {"shop"}, 5).startswith("not approved"), f"not approved: {name}")
bad = steps(plan("urn:kk:plan:p:step:2", "urn:kk:action:delete-everything", "shop", 1), approved)[0]
check(refusal(bad, {"shop"}, 5).startswith("the action is not on the fixed list"), "refused: an action not on the list")
print(f"M2-T6 actuator checks passed ({ok}/{ok})")
