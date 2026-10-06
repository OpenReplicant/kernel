# 0029. Building the approval channel

Date: 2026-10-06 · Status: accepted (amends ADR 0019, which it builds)

## Context

ADR 0019 designed the approval channel: people approve, the actuator enforces, the kernel
records. Phase 3 needs it built (ADR 0026, item 3), because the closed loop ends in the
system proposing changes to what it deployed. Building it raised four points 0019 left
open.

1. **Approvals in the world.** Not every `approved_by` is a decision of the kernel's. The
   process and bpm packs record "the invoice was approved by the controller", a fact
   reported about the world. Governing every `approved_by` edge would break that.
2. **Proposals counted as support.** Belief counts every assertion whatever its modality.
   A `proposed` claim that asserted the edge it proposes would count as support for it.
3. **Binding a person.** Something the gateway's writer cannot fake has to tell
   `kernel.write` that a signed-in person is deciding.
4. **The instrument set.** It needs a representation that replays from the log.

## Decision

**Governance applies to proposals.** A proposal is a Claim node of kind `proposed`.
Decisions are the operations that settle one:
- an `approved_by` or `rejected_by` edge from it;
- a transition of it to `approved` or `rejected`.

`approved_by` between entities, events and agents stays an ordinary fact, reported or
observed like any other.

**Proposals and hypotheses are data, not facts.** A claim of modality `proposed` or
`hypothetical` may promote itself and assert edges that start or end at its own Claim
node. Those edges say who reports it, and what supports or contradicts it. It may not
create nodes, assert facts between other nodes, or transition anything; that is refused
(`core.proposals_are_data`). The change a proposal describes goes in `props.change`. So
belief never counts what someone merely proposes or supposes.

**A proposal's status is decided, not believed.** Promoting a proposal records no status
assertion: it starts `open`, and only decisions and its writer's withdrawal assert a
status. Otherwise the proposer's "open" and the person's "approved" would be two sources
disagreeing, and the status would read as contested.

**`kernel.decide` is the only way to decide.**
- **Who calls it:** it runs only as `kernel_approver`, a role no gateway login holds.
- **Who decides:** the person comes from the verified token PostgREST puts in
  `request.jwt.claims`, never from an argument.
  - The token's email maps to the human agent with that email identity key.
  - On a person's first decision their agent self-registers.
- **What it does:** it builds the decision and writes it through `kernel.write`.
- **Actions:**
  - `approve` and `reject` a proposal, with an optional reason.
  - `protect` a set of nodes as instruments, below.
  - `withdraw` is the proposer's, not a decision, and stays with `kernel.write`.

**The governance rules**, a new rule category that `kernel.write` checks:
- `core.decided_by_person`:
  - Decisions are written only in a session whose role is `kernel_approver`, by the
    human agent its token names.
  - The edge points at that agent.
  - A machine never decides, under any profile.
  - A gateway writer that sets the same session variables still fails, because its
    role is not the approver's.
- `core.no_self_approval`: the person deciding did not write the proposal.
- `core.approver_outside_system`: the person is not `part_of` a `system` that the
  proposal is about, or that something it is about is `part_of` (the self boundary,
  ADR 0017).
- `core.instruments_need_two`:
  - A proposal about an instrument becomes `approved` only once two distinct people
    approve it.
  - The first approval is recorded and leaves it open.
- `core.instruments_alone`: a proposal about an instrument and about anything else is
  refused at approval and must be split.
- `core.withdraw_own`: only a proposal's writer moves it to `withdrawn`.

**Instruments are protected by claims.** `decide({"action": "protect", "about": [...]})`
writes a `normative` claim, by the person, `about` the nodes it protects, with
`props.instruments` true.
- A node is an instrument while such a claim is about it.
- The protecting claims are instruments themselves. Only a decision can write one,
  so the system cannot shrink the set; it can only ask, through a proposal two people
  approve.
- Removing protection is not built: it waits for the first need.
- Repository paths (CI, fixtures, rules) are protected where the actuator enforces them:
  CODEOWNERS and branch protection (ADR 0019).

**Order of work.**
1. This change: the kernel (0.6.0). It adds `kernel.decide`, the governance rules,
   `kernel_approver` and the tests.
2. The explorer's signed-in Proposals page. PostgREST verifies the token (a static
   one-person token locally, an OpenID Connect provider such as Forgejo or Keycloak
   otherwise) and calls `kernel.decide`.
3. The forge adapter. Pull requests, reviews and merges are ingested as observed claims
   about the decision, citing it. A forge's record is evidence of a decision, never the
   decision. With it comes the query "changes deployed without an approved proposal".

## Consequences

Invariant 10 holds as written. Agents never approve, and now the kernel refuses them
rather than trusting them to refrain. Invariant 1 is unchanged: `kernel.decide` writes
through `kernel.write`, and the log holds the same seven operations.

Writers lose one freedom: a proposed or hypothetical claim can no longer assert facts
about other nodes. No fixture or pack used it.

Until the Proposals page exists, a decision needs a session that can take the approver
role: an operator's, in tests. The actuator's branch protection remains the gate for code
(ADR 0019).
