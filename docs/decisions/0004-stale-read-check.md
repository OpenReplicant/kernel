# 0004. Stale reads are judged per node, against other agents' changes

Date: 2026-10-04 · Status: accepted

## Decision

Every log entry records the nodes it changed, including both endpoints of every edge it
asserted on (`kernel.node_touches`). A write is stale when a node it touches (other than
nodes it creates) was touched after `read_at_offset` by a different agent. An agent's own
later writes do not make it stale, so it can write claim after claim from one read.

`read_at_offset` is required. Read tools return `head_offset`, taken before the read, so
the offset never claims more than the read saw.

## Consequences

Concurrent agents writing about the same nodes must re-read on rejection; the problem
document lists the changed nodes and the head offset. Sessions sharing one agent id are
not protected from each other; profiles give each configuration its own agent.
