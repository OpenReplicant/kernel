# 0019. The approval channel: people approve, the actuator enforces, the kernel records

Date: 2026-10-04 · Status: accepted; built by [ADR 0029](0029-building-the-approval-channel.md), which amends it

## Context

The system is meant to improve things, its own code and configuration included, through
observe → propose → test → ask → apply. The design keeps its organisation in human hands:
"it may change its structure, but its organisation (kernel invariants, evals, setpoints,
the approval channel) stays in human hands. Every step produces proposals, never direct
changes." Three failure modes have to be designed out, not hoped away:

1. **Self-approval.** Today any writer can transition a proposal to `approved` and assert
   `approved_by` to any agent, a human included: the gateway's machine agent could write
   an approval in a person's name. Nothing in the kernel stops it.
2. **Reward hacking the observers.** If one proposal can change both a thing and the check
   that judges it (an eval fixture, a CI job, a rule, a setpoint), passing the check proves
   nothing.
3. **Recording is not enforcing.** The kernel is passive (invariant 10). An approval in the
   graph does not stop a change from reaching production; only the system that applies
   changes can.

## Decision

**Proposals.** A proposed change is a promoted Claim of modality `proposed`, `about` what
it changes, with the change itself as data in `props.change`:
- a pull request (URL, head commit);
- a kernel payload to submit after approval (from a fork, ADR 0018);
- a pack manifest version;
- a setting.

Evidence for it is `verified_by` edges to test events (CI runs, canaries, fork
comparisons) and `supports` or `contradicts` from other claims.

**Approvals are decisions written by the person who makes them.** A new kernel rule
category, `governance`, checked inside `kernel.write`, applies to every `approved_by` or
`rejected_by` edge and every transition of a `proposed` or `normative` claim to
`approved` or `rejected`:
- The writing agent must be the agent the edge points to, and of kind `human`. Machine
  agents never approve, under any profile.
- The writing session must hold `kernel_approver`, a role no gateway login has. The
  approver's agent is bound to the authenticated person, not to a request argument. The
  explorer's PostgREST verifies an identity provider's token and switches to
  `kernel_approver`; `kernel.write` reads the verified claims (`request.jwt.claims`) and
  maps the person's email to their human Agent by identity key. A machine writer that
  sends a person's agent id gets a governance rejection.
- The approver must not be the agent that wrote the proposal (no self-approval).
- The approver must not be `part_of` the system the proposal changes, by the accepted
  self-boundary claims (ADR 0017). This fails safe: the system can only make approvals
  harder by drawing its boundary wider, never easier, because machines cannot approve at
  all.

The explorer gains a signed-in Proposals page; approving there is the first write a
browser can make, through the same `kernel.write`.

**Instruments are a protected tier.** The instruments are the things that judge changes:
- eval fixtures and expected graphs;
- CI configuration;
- ontology rules and pack manifests;
- setpoints;
- profiles and the approval rules themselves.

Each instrument is identified by a node or a repository path. The set is a normative
claim that only an approver can write, so the system cannot shrink it. A proposal `about`
an instrument needs two approvals from distinct people. A proposal may not change an
instrument and anything it checks in the same proposal: it is refused at approval time
and must be split. Verification that counts toward approval must come from an agent other
than the proposer: `verified_by` events written by CI's own machine agent, with its own
credential. Vital signs are measured by the kernel, never reported by the agents judged.

**The actuator enforces; the kernel records and checks conformance.**
- Code reaches the default branch only through a pull request with required human review,
  CODEOWNERS on instrument paths, and branch protection.
- Deployments need an environment with required reviewers.
- Agents hold no credential that can merge, push to a protected branch or deploy.

Decisions made in the actuator (a GitHub review) are ingested as observed claims about
that decision, citing it. They are evidence of the decision, not the decision: a
machine's report of an approval never transitions a proposal's status. The kernel's part
is to make violations visible. "Changes deployed without an approved proposal" is a
query over observed deployment events and approved proposals, and later a vital sign.

**Self-modification is not a special case.** Changes to this repository, its skills, its
packs, its profiles or the self boundary are proposals like any other, with instruments
in the protected tier. Pack installation stays an operator action outside `kernel.write`.

**Approval by policy** (the design's auto-approval of the lowest-risk tier) is not built
yet. When it is, the policy is a normative claim written by an approver, it counts as an
instrument, and each approval it grants names the person who wrote the policy.

## Consequences

Building this changes the kernel:
- a `governance` rule category in `kernel.write`;
- the `kernel_approver` role, and an authenticator login for PostgREST with token
  verification;
- an identity key on human agents that matches the identity provider's claim;
- the instrument set as a normative claim.

Tests come with it: a machine cannot approve; nobody approves their own proposal; a
forged agent id is refused; an instrument proposal needs two people; a mixed proposal is
refused.

Until it is built, approvals in the graph are claims, not decisions. The actuator's
protection is the only real gate and should be configured now: branch protection on
`main`, required reviews, and CODEOWNERS on `kernel/sql/`, `evals/`, `packs/*/evals/`,
`packs/*/rules.yaml`, `.github/` and `profiles/`.

Identity-provider setup is deployment work. Local stacks can use a static token for one
person, never shared with an agent.
