# 0003. Single-valued conflicts: a rule within a source, belief across sources

Date: 2026-10-04 · Status: accepted

## Context

The design both rejects cardinality violations ("role_approver already has implements
from agt_dana for 2026-03-01 onward") and says that, on a single-valued edge, "a claim
from a different source makes the fact contested", never resolved by overwriting.

## Decision

- Belief groups assertions by source (`source_key`: the source's collection, else the
  source, else the writing agent). Each source counts through its latest assertion; that
  is how a newer claim from the same source supersedes.
- After a write's operations are projected, each cardinality rule is checked per source.
  If the writing source's own view would give the key node two holders at once, the write
  is rejected (`urn:wmk:rule:cardinality`) and names the other edge, so the model can
  close it in the same payload.
- If the overlap exists only because other sources hold the other edge open, the write is
  accepted and the pair is recorded as a conflict in the log entry (`conflicts`). Both
  edges are contested while the conflict is active (neither edge rejected, windows
  overlapping). It clears when a source closes a window, never by overwriting.
- An edge's window is the union of what its counted sources assert; when they disagree
  the edge is contested and `window_agreed` is false.

## Consequences

Within one source the graph stays consistent; across sources disagreement is visible with
both sides kept. Recording conflicts in the log keeps replay exact even if rules change.
