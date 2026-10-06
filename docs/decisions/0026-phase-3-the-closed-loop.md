# 0026. Phase 3: the closed loop, on a substrate that senses

Date: 2026-10-06 · Status: accepted (direction; each item below gets its own ADR before code)

## Context

Phases 1 and 2 built and measured the substrate:
- the log, computed belief, quotes, extraction runs, origins and erasure;
- a gateway any harness can use;
- packs for research and software;
- an explorer;
- evals, including one against independent annotators.

The goals it serves:
- map any business, so that its work can be automated;
- map software and systems, so that they can be monitored and improved;
- map the system itself, so that an agent can propose changes to it;
- become the substrate of a cognitive model.

What Phase 2 taught:
- **The kernel holds up.** Invariants, replay and erasure work under tests and live runs.
- **Extraction quality comes from the model.** The weak step is judgement, which the kernel
  cannot check.
- **Agent loops are the wrong shape for bulk work.** Most of the cost was harness overhead.
- **Existing MCP servers cover the edges:**
  - parsers (docling-mcp, markitdown-mcp);
  - process mining (pm4py-mcp);
  - process runtimes (Camunda, n8n);
  - forges and clusters.

  What none of them provides is a validated, sourced, versioned record of belief shared by
  every agent. That is the kernel's job.
- **Reputation comes first.** An open demonstration of the whole loop is how to earn the
  reputation that leads to client work.

## Decision

**Phase 3 takes one synthetic company (Northwind) around the whole loop, as an open
demo.**
1. Map it from interviews, documents and event logs.
2. Rank what to automate.
3. Automate one part on a process runtime.
4. Observe what runs and check it against the map.
5. Let the system propose changes to what it deployed, approved by a person.

**The process representation is runtime-neutral, not BPMN.**
- Steps are `activity` entities (a core kind) joined by `flows_to` edges (a kernel edge)
  that carry a `condition`. Decisions may use `gateway` entities.
- Roles, actors, systems, data, events, handoffs, exceptions and KPIs come from the pack.
- Runtimes disagree on representation: Camunda runs BPMN, n8n runs node graphs, RuleGo
  runs rule chains, and code runs code. The kernel keeps the common denominator, and each
  runtime gets a workflow adapter that translates both ways. BPMN import and export, if
  wanted, is one more adapter.

**Three views of how work happens, each from its own sources and origins.**
- *As told:* interviews.
- *As written:* SOPs and documents.
- *As done:* exports and event logs (CSV, XES, OCEL 2.0). Process discovery and
  conformance checking come from an existing server (pm4py-mcp), and their results are
  ingested as observed claims.

Where the views disagree, belief marks the fact contested, and the discovery deliverable
is built from those contested facts.

**Workflow adapters, one per runtime.** Each adapter:
- compiles a process into its runtime's configuration, as a proposal;
- deploys it only once the proposal is approved;
- reads deployments and runs back as observed claims and events.

Conformance is then a query: runs compared with the mapped process.

**Systems are observed, not just declared.** A sysops and self pack extends the software
pack. Deterministic observation adapters read deployments, versions, health and changes
from Kubernetes, Docker, Grafana or Prometheus and the forge. Drift is the declared view
contradicted by the observed one, so it shows up as a contested fact like any other.

**The approval channel is built (ADR 0019).** People approve, the actuator enforces, the
kernel records.
- Code and configuration go through a forge with branch protection and required reviews.
  Options: GitHub; a self-hosted Forgejo, which also serves as an OpenID Connect provider
  for a self-contained stack; or GitLab, where a client already runs it. The forge's
  decisions are ingested as observed claims about the decision.
- Decisions that are not code go through the explorer's signed-in Proposals page:
  activating an automation, an erasure, a policy. Identity comes from an OIDC provider:
  the forge, or the client's own (Keycloak, Entra ID, Google).

**Invariant 10 is amended.** The kernel and the gateway stay passive. Adapters that change
a runtime, a repository or a system live in packs, act only on approved changes, and hold
no credential that bypasses approval. Agents never approve.

**Also in scope:**
- the extractor, as a CLI and an MCP server on configured model endpoints (ADR 0024,
  revised);
- the research eval improvements;
- a proposed ADR for recall into context, the read side of context engineering.

**Open and private.** This repository stays public: the kernel, the general packs, the
demo and the evals. Engagement-derived packs, enterprise adapters, playbooks, pricing and
client datasets live in private repositories that install packs from local folders. The
kernel never depends on them, and public CI never references them. The licence is the
owner's decision.

**The north star.** The cognitive model improves itself by reading new research (the
research pack over the daily arXiv stream) and testing techniques against its own evals.
It comes after Phase 3.

## Consequences

CLAUDE.md is rewritten for Phase 3.

Out-of-scope items brought into scope:
- workflows, as workflow adapters;
- a minimal ops loop: observe, compare, propose, approve;
- the process product's foundation, as a general pack.

Still out of scope:
- a pack registry, and pack SQL;
- automatic capture of agents' own sessions;
- vital signs beyond the demo's health and drift checks;
- generative ops strategies beyond proposing a pull request;
- concept formation, habit formation, and the cognitive model itself.

The next ADRs, in order:
1. the process pack and the event-log adapter;
2. the sysops and self pack;
3. building the approval channel, amending ADR 0019 with the adapters above;
4. workflow adapters, with the first runtime chosen;
5. the extractor;
6. recall into context.
