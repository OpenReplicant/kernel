"""D-79 unit checks: trigger patterns read from manifests and turned into SQL over api.claims."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kk.lib import PREFIXES, Pattern, manifest_patterns  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


w, args = Pattern("? bbo:has_nextFlowNode ?").where()
check(w == "c.predicate = %s" and args == [PREFIXES["bbo"] + "has_nextFlowNode"], "a CURIE predicate, anything else")
w, args = Pattern("? dcterms:type urn:kk:discrepancy-kind:*").where()
check("like %s" in w and args[1] == "urn:kk:discrepancy-kind:%", "a prefix ending in * matches by prefix")
check(Pattern("? ? urn:a_b%*").where()[1] == ["urn:a\\_b\\%%"], "LIKE wildcards in a prefix are escaped")
check(Pattern("? ? ?").where() == ("true", []), "? ? ? fits every claim")
try:
    Pattern("? rdf:type")
    check(False, "two terms refused")
except ValueError:
    check(True, "a pattern needs three terms")
learner = [p.spec for p in manifest_patterns("learner-process")]
check(learner == ["? dcterms:type urn:kk:discrepancy-kind:*"], f"the learner wakes on applied discrepancies: {learner}")
check(manifest_patterns("recall-index") == [], "a module without pattern triggers has none")
print(f"D-79 trigger pattern checks passed ({ok}/{ok})")
