# 0011. A report weighs no more than its speaker

Date: 2026-10-04 · Status: accepted

## Context

ADR 0007 weighs every assertion by the trust of the agent that wrote it. In an interview
the assistant writes claims the person reported, and when an assistant relays a web page
it writes claims an unknown author made. Writer trust overstates the second case: a
medium-trust assistant turns a low-trust tip into a medium-trust fact. Using only the
speaker's trust would fix that but open the reverse hole: a low-trust agent could cite a
high-trust person's source to borrow their weight.

## Decision

Each claim records the trust level that weighs its assertions (`kernel.claims.trust`,
`claim.trust` in the log entry):

- `observed` and `inferred` claims: the writer's trust (the writer saw or reasoned it).
- `reported` claims citing a source with an `author` agent: the lower of the writer's and
  the author's trust. A report is no more credible than its speaker or its transcriber.
- `reported` claims without an author: the writer's trust.

Trust is resolved by `kernel.write` and stored in the entry, so replay never reads agents'
current state. `belief_v1` is unchanged: only its recorded input is.

## Consequences

A person's interview turns count at their trust when the assistant is trusted at least as
much; raise the assistant's `trust_level` in the profile to let a high-trust person's
reports count fully. Entries written before this change carry no claim trust and replay
with the writer's, exactly as before. Readers see the trust used in `query_log` results.
