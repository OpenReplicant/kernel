---
name: adaptation-planning
description: Use when the Knowledge Kernel shows anomalies on a running service (latency over its limit, unusual values) and an adaptation should be proposed — scaling or restarting a service — for a person to approve before the actuator carries it out.
---

# Planning an adaptation (MAPE-K: Plan)

Monitoring (the metrics observer) and analysis (the anomaly processor) are modules; **you plan**, a
**person approves**, the **actuator executes** and the observer records the effect. You never act on
the system yourself, and you never claim that a plan worked: the next observations say whether it did.

## 1. Read the analysis

- `query_claims(predicate="http://www.w3.org/ns/oa#hasTarget", object_entity="urn:kk:node:<service>")`
  → the anomalies on a service (`urn:kk:anomaly:*`); `get_entity(<anomaly>)` → its kind
  (`dcterms:type` `urn:kk:anomaly-kind:limit|zscore`), its sentence (`oa:bodyValue`) and window.
- `explain(<anomaly claim>)` → the observations and limit it was derived from. Read the latest
  windows of the service (`get_entity("urn:kk:node:<service>")`: `sosa:hasSimpleResult` claims with
  `sosa:observedProperty` `replicas-up`, `request-rate`, `latency-seconds-mean`).
- Plan only for anomalies from the latest windows, and only once: look for an existing plan that
  cites them (`query_claims(predicate="http://purl.org/net/p-plan#isStepOfPlan")`).

## 2. Choose from the fixed action list

| Action | When | Step claims |
| --- | --- | --- |
| `urn:kk:action:scale` | load-driven: latency rose with the request rate | `sosa:hasSimpleResult` the replica count (`xsd:integer`, 1 to the actuator's maximum, 5 by default) |
| `urn:kk:action:restart` | latency or errors rose without more load | none |

Anything else (config changes, new services, deletes) is not on the list: say so to the person instead.
Size a scale from the observations: replicas ≈ ceil(request rate ÷ the rate one replica handled within
its limit), at least current + 1.

## 3. Record the plan (one change set, `lane: "review"`)

Full IRIs; `SOSA` = `http://www.w3.org/ns/sosa/`, `PPLAN` = `http://purl.org/net/p-plan#`.

- `urn:kk:plan:<uuid>` `rdf:type` `PPLAN Plan`; `rdfs:label` "Scale shop to 3 replicas (latency over limit)".
- `urn:kk:plan:<uuid>:step:1` `rdf:type` `SOSA Actuation`; `PPLAN isStepOfPlan` the plan;
  `dcterms:type` the action; `SOSA hasFeatureOfInterest` `urn:kk:node:<service>`; for scale,
  `SOSA hasSimpleResult` the count.
- Every claim `evidence_type: inferred`, `derived_from` the anomaly claims (and the observations) you
  planned from — the timeline of the loop is built from these links.
- `lane: "review"`, target scope `urn:kk:scope:default`, idempotency key `plan:<anomaly iri>`.

The actuator only carries out steps whose change set a person other than you approved in the review
lane. Tell the person what you proposed and why, citing the anomaly and observation claim ids with their
belief, and that it waits in Review.
