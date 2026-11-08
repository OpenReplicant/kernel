"""map-drift checks (D-100): gaps between the declared map and observed calls, only where evidence could exist.

  python modules/map-drift/tests/test_compare.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from map_drift.compare import KKT, SKOS, declared, discrepancies, observed, predicates, settings  # noqa: E402

BS = "https://w3id.org/knowledge-kernel/backstage#"
N = "urn:kk:node:"
checks = []


def check(name, ok):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name)


dep, req, cap = predicates([
    {"subject": BS + "dependsOn", "predicate": SKOS + "closeMatch", "object_entity": KKT + "dependsOn"},
    {"subject": BS + "consumesApi", "predicate": SKOS + "relatedMatch", "object_entity": KKT + "hasRequirement"},
    {"subject": BS + "providesApi", "predicate": SKOS + "closeMatch", "object_entity": KKT + "hasCapability"},
    {"subject": BS + "partOf", "predicate": SKOS + "closeMatch", "object_entity": KKT + "memberOf"}])
check("predicates mapped to kk-tosca's count as theirs; others do not",
      BS + "dependsOn" in dep and BS + "consumesApi" in req and BS + "providesApi" in cap
      and BS + "partOf" not in dep | req | cap)

claims = [{"id": "d1", "subject": N + "agent", "predicate": BS + "consumesApi", "object_entity": "urn:kk:api:model-api"},
          {"id": "d2", "subject": N + "gateway", "predicate": BS + "providesApi", "object_entity": "urn:kk:api:model-api"},
          {"id": "d3", "subject": N + "gateway", "predicate": BS + "dependsOn", "object_entity": N + "postgres"},
          {"id": "d4", "subject": N + "agent", "predicate": BS + "consumesApi", "object_entity": "urn:kk:api:catalog-api"},
          {"id": "d5", "subject": N + "catalog", "predicate": BS + "providesApi", "object_entity": "urn:kk:api:catalog-api"},
          {"id": "d6", "subject": N + "agent", "predicate": BS + "consumesApi", "object_entity": "urn:kk:api:judge-api"},
          {"id": "d7", "subject": N + "judge", "predicate": BS + "providesApi", "object_entity": "urn:kk:api:judge-api"},
          {"id": "d8", "subject": N + "cron", "predicate": BS + "dependsOn", "object_entity": N + "agent"}]
want = declared(claims, dep, req, cap)
check("a consumer and a provider of one interface are a declared call, citing both claims",
      want[(N + "agent", N + "gateway")] == {"via": ["model-api"], "premises": ["d1", "d2"]})
check("dependsOn is a declared call", want[(N + "gateway", N + "postgres")]["premises"] == ["d3"])

alias, untraced = settings([
    {"subject": N + "postgres", "key": "example.dev/otel-service", "value": "db"},
    {"subject": N + "agent", "key": "substrate.dev/untraced", "value": "judge-api"}])
check("names from the map's otel-service and untraced annotations",
      alias == {N + "db": N + "postgres"} and untraced == {N + "agent": {"judge-api"}})

seen = observed([{"id": "o1", "subject": N + "agent", "object_entity": N + "gateway", "valid_to": "2026-11-08T00:01"},
                 {"id": "o2", "subject": N + "agent", "object_entity": N + "gateway", "valid_to": "2026-11-08T00:02"},
                 {"id": "o3", "subject": N + "gateway", "object_entity": N + "anthropic", "valid_to": "2026-11-08T00:02"},
                 {"id": "o4", "subject": N + "gateway", "object_entity": N + "db", "valid_to": "2026-11-08T00:02"},
                 {"id": "o5", "subject": N + "catalog", "object_entity": N + "db", "valid_to": "2026-11-08T00:02"}], alias)
check("observed names resolve to the map's nodes; the latest observation of a pair is kept",
      seen == {(N + "agent", N + "gateway"): "o2", (N + "gateway", N + "anthropic"): "o3",
               (N + "gateway", N + "postgres"): "o4", (N + "catalog", N + "postgres"): "o5"})

gaps = {(d["kind"].rsplit(":", 1)[-1], d["source"], d["target"]): d for d in discrepancies(want, seen, untraced)}
check("a call the map does not declare is a gap", ("undeclared-call", N + "gateway", N + "anthropic") in gaps
      and ("undeclared-call", N + "catalog", N + "postgres") in gaps)
check("it cites the observed call and what the map declares about the caller",
      gaps[("undeclared-call", N + "gateway", N + "anthropic")]["premises"] == ["o3", "d3"]
      and gaps[("undeclared-call", N + "catalog", N + "postgres")]["premises"] == ["o5"])
miss = gaps.get(("unobserved-call", N + "agent", N + "catalog"))
check("a declared call between two active parts, not observed, is a gap, citing both sides",
      miss and miss["premises"][:2] == ["d4", "d5"] and "o2" in miss["premises"] and "o5" in miss["premises"])
check("an interface the source marks untraced is not a gap", ("unobserved-call", N + "agent", N + "judge") not in gaps)
check("a source that sends nothing is not a gap", ("unobserved-call", N + "cron", N + "agent") not in gaps)
check("declared and observed calls agree: no gap", not any(k[1:] in {(N + "agent", N + "gateway"),
                                                                      (N + "gateway", N + "postgres")} for k in gaps))
check("exactly these three gaps", len(gaps) == 3)
again = {d["iri"] for d in discrepancies(want, seen, untraced)}
check("a gap keeps its identity", again == {d["iri"] for d in gaps.values()})

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
