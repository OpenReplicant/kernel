# 0010. Agents can be part of entities

Date: 2026-10-04 · Status: accepted

## Context

In the first live-harness run, "Omar moved to the audit team" stayed unresolved: no kernel
edge let a person belong to a team. `part_of` started only at entities and events, and the
other options (inventing a "member" role, or `participates_in`, which needs an event) misstate
the fact.

## Decision

`part_of` may start at an Agent: a person or machine agent is part of a team, department or
organisation, with a validity window like any other edge. The kernel edge list is unchanged;
only the node-type domain of `part_of` widens. Packs restrict it further as they need.

## Consequences

Membership is a dated, sourced, contestable fact like role assignments. Existing logs replay
unchanged: no earlier write could have used the new domain.
