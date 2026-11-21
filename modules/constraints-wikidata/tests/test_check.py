#!/usr/bin/env python3
"""D-108 unit checks: Wikidata's property constraints over plain rows, with no database and no model."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from constraints_wikidata.check import (ALLOWED, CLASS, CONSTRAINT, INSTANCE_OF, KINDS, PATTERN, SUBCLASS_OF,  # noqa: E402
                                        WD, WDT, short, verdicts, violations)

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


Q = "urn:kk:substrate:"
SINGLE, FORMAT, ONEOF, VTYPE = (WD + q for q in ("Q19474404", "Q21502404", "Q21510859", "Q21510865"))
n = [0]


def row(subject, predicate, entity=None, value=None, quals=None):
    n[0] += 1
    return {"id": f"00000000-0000-0000-0000-{n[0]:012d}", "subject": subject, "predicate": predicate,
            "object_entity": entity, "object_value": {"@value": value} if isinstance(value, str) else value,
            "qualifiers": quals or {}}


def constraint(prop, kind, **quals):
    return row(prop, CONSTRAINT, entity=kind, quals=quals)


def kinds(found, claim):
    return [(v["kind"]) for v in found.get(claim["id"], [])]


check(set(KINDS.values()) == {"single-value", "format", "one-of", "value-type"}, "the four kinds Substrate's store checks")
check(short(Q + "Q13") == "Q13" and short(WDT + "P527") == "P527" and short("https://x.example/ns#metric") == "metric",
      "short ids: a Qid, a Wikidata P number, the tail of an IRI")

# single-value
P = Q + "P"
a1, a2, b1 = row(Q + "Q1", P, value="x"), row(Q + "Q1", P, value="y"), row(Q + "Q2", P, value="z")
f = violations([constraint(P, SINGLE)], [a1, a2, b1])
check(kinds(f, a1) == kinds(f, a2) == ["single-value"] and not f.get(b1["id"]), "single-value: every value of a subject with two, not a lone one")
check(f[a1["id"]][0]["message"] == "Q1 has 2 usable values for P; it takes one", "single-value: the message counts the values")
c2 = [constraint(P, SINGLE), constraint(P, SINGLE)]
check(len(violations(c2, [a1, a2])[a1["id"]]) == 2, "two single-value constraints on a property: one violation each, as Substrate's rows")
check(violations([constraint(WDT + "P1", SINGLE)], [a1, a2]) == {}, "a constraint on another property checks nothing here")
check(violations([constraint(P, WD + "Q9999999")], [a1, a2]) == {}, "a constraint kind this module does not check is not a violation")

# format
M = WDT + "metric"
ok1, bad1 = row(Q + "Q1", M, value="p99"), row(Q + "Q1", M, value="latency")
f = violations([constraint(M, FORMAT, **{PATTERN: "p[0-9]+"})], [ok1, bad1])
check(kinds(f, bad1) == ["format"] and ok1["id"] not in f, "format: matched as a whole, so a string off the pattern is reported")
check(f[bad1["id"]][0]["message"] == '"latency" does not match the format p[0-9]+', "format: the message quotes the value as Substrate's store does")
part = row(Q + "Q1", M, value="p99x")
check(kinds(violations([constraint(M, FORMAT, **{PATTERN: "p[0-9]+"})], [part]), part) == ["format"], "format: a partial match is not a match")
two = violations([constraint(M, FORMAT, **{PATTERN: ["p[0-9]+", "q[0-9]+"]})], [ok1])
check(kinds(two, ok1) == ["format"], "format: several patterns are each required")
typed = [row(Q + "Q1", M, value={"@value": "12", "@type": "http://www.w3.org/2001/XMLSchema#decimal"}),
         row(Q + "Q1", M, entity=Q + "Q7"), row(Q + "Q1", M, value={"@value": "http://x", "@type": "http://www.w3.org/2001/XMLSchema#anyURI"})]
f = violations([constraint(M, FORMAT, **{PATTERN: "p[0-9]+"})], typed)
check(set(f) == {typed[2]["id"]}, "format: only strings and URLs are matched; a quantity or an entity is not")
bad = []
check(violations([constraint(M, FORMAT, **{PATTERN: "["})], [ok1], problems=bad) == {} and len(bad) == 1 and bad[0]["pattern"] == "[",
      "format: a pattern that is not a regular expression checks nothing and is reported")
check(violations([constraint(M, FORMAT)], [ok1]) == {}, "format: no pattern, nothing to break")

# one-of
V = WDT + "verdict"
v_ok, v_bad = row(Q + "Q1", V, entity=Q + "Q5"), row(Q + "Q1", V, entity=Q + "Q6")
f = violations([constraint(V, ONEOF, **{ALLOWED: [Q + "Q5", Q + "Q8"]})], [v_ok, v_bad])
check(kinds(f, v_bad) == ["one-of"] and v_ok["id"] not in f, "one-of: a value outside the listed ones is reported")
check(f[v_bad["id"]][0]["message"] == "Q6 is not one of the values verdict allows", "one-of: the message")
f = violations([constraint(V, ONEOF, **{ALLOWED: Q + "Q5"})], [v_ok, v_bad])
check(kinds(f, v_bad) == ["one-of"] and v_ok["id"] not in f, "one-of: a single allowed value is a plain value, not a list")
check(kinds(violations([constraint(V, ONEOF)], [v_ok]), v_ok) == ["one-of"], "one-of: with nothing allowed, every entity value is outside")
check(violations([constraint(V, ONEOF, **{ALLOWED: [Q + "Q5"]})], [row(Q + "Q1", V, value="text")]) == {}, "one-of: a literal is not checked")

# value-type
PT = WDT + "part"
CLS, SUP, OTHER = Q + "Q20", Q + "Q21", Q + "Q22"
tree = [row(Q + "Q10", INSTANCE_OF, entity=CLS), row(Q + "Q11", INSTANCE_OF, entity=OTHER), row(CLS, SUBCLASS_OF, entity=SUP),
        row(SUP, SUBCLASS_OF, entity=Q + "Q23"), row(Q + "Q12", INSTANCE_OF, entity=Q + "Q10")]
direct, wrong, none_, deep = (row(Q + "Q1", PT, entity=e) for e in (Q + "Q10", Q + "Q11", Q + "Q13", Q + "Q10"))
k = lambda cls: violations([constraint(PT, VTYPE, **{CLASS: cls})], [direct, wrong, none_], tree)  # noqa: E731
check(set(k(CLS)) == {wrong["id"], none_["id"]}, "value-type: an instance of the class is fine; another class and no class are reported")
check(set(k(SUP)) == {wrong["id"], none_["id"]}, "value-type: an instance of a subclass is an instance of the class")
check(set(k(Q + "Q23")) == {wrong["id"], none_["id"]}, "value-type: subclass of is followed upward to any depth")
check(set(k([OTHER, CLS])) == {none_["id"]}, "value-type: any of several classes will do")
check(set(k(OTHER)) == {direct["id"], none_["id"]}, "value-type: an instance of the wrong class is reported")
check(k(CLS)[wrong["id"]][0]["message"] == "Q11 is not an instance of a class part allows", "value-type: the message")
check(violations([constraint(PT, VTYPE, **{CLASS: CLS})], [direct], [row(Q + "Q10", INSTANCE_OF, value="x")]) != {},
      "value-type: only entity values count as classes")
loop = [row(Q + "Q10", INSTANCE_OF, entity=CLS), row(CLS, SUBCLASS_OF, entity=SUP), row(SUP, SUBCLASS_OF, entity=CLS)]
check(set(violations([constraint(PT, VTYPE, **{CLASS: OTHER})], [direct], loop)) == {direct["id"]}, "value-type: a subclass loop ends")

# several constraints on one claim, order, ids
both = [constraint(M, FORMAT, **{PATTERN: "p[0-9]+"}), constraint(M, ONEOF, **{ALLOWED: [Q + "Q5"]})]
ent = row(Q + "Q1", M, entity=Q + "Q6")
text = row(Q + "Q1", M, value="latency")
f = violations(both, [ent, text])
check(kinds(f, ent) == ["one-of"] and kinds(f, text) == ["format"], "each constraint looks only at the values it is about")
check(f[text["id"]][0]["constraint"] == both[0]["id"] and f[text["id"]][0]["property"] == M, "a violation names the constraint's claim and the property")
check(violations(both, [ent, text]) == violations(both, [text, ent]), "the answer does not depend on the order of the rows")
check(violations([], [a1, a2]) == {} and violations([constraint(P, SINGLE)], []) == {}, "no constraints or no claims: nothing")

# verdicts: report, clear, nothing to do
v1 = [{"constraint": "c1", "kind": "format", "property": M, "message": "m"}]
check(verdicts({"x": v1}, {}) == [("x", "inconclusive", {"check": "property constraints", "violations": v1})],
      "a new violation is recorded as inconclusive, with the violations in the details")
check(verdicts({"x": v1}, {"x": ("inconclusive", v1)}) == [], "the same violation again: nothing to record")
v2 = [dict(v1[0], message="changed")]
check(verdicts({"x": v2}, {"x": ("inconclusive", v1)})[0][1] == "inconclusive", "a changed reason is recorded again")
check(verdicts({}, {"x": ("inconclusive", v1)}) == [("x", "supported", {"check": "property constraints", "violations": []})],
      "no longer a violation: supported, with none")
check(verdicts({}, {"x": ("supported", [])}) == [] and verdicts({}, {}) == [], "cleared already, or never reported: nothing")
check(verdicts({"x": v1}, {"x": ("supported", [])})[0][1] == "inconclusive", "reported again after it was cleared")
check({v for _, v, _ in verdicts({"x": v1}, {"y": ("inconclusive", v1)})} == {"inconclusive", "supported"}
      and "refuted" not in {v for _, v, _ in verdicts({"x": v1}, {})}, "a report is inconclusive, never refuted")

print(f"{ok} checks passed")
