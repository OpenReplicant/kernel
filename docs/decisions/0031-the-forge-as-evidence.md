# 0031. The forge as evidence: pull requests, reviews and merges, and changes without approval

Date: 2026-10-06 · Status: accepted (builds ADR 0029's third step)

## Context

Code reaches the default branch through the forge. There, branch protection and required
reviews are the gate (ADR 0019). The kernel's part is to record what the forge did and to
make violations visible: "changes deployed without an approved proposal". ADR 0029 left
this as its third step. Building it raises four questions:

- what to read from the forge;
- how to write it without turning a forge's record into a decision;
- how to handle the people it names;
- where the query lives, since packs carry no SQL.

## Decision

**Capture, then map.** As with Docker (ADR 0028), `wmk-software forge capture OWNER/REPO`
reads the forge into a JSON capture. `forge map` writes that capture through the gateway.
Tests and CI replay recorded captures.

**What a capture reads.**
- **Endpoints:** three read-only GitHub REST endpoints:
  - the repository;
  - its pull requests, newest first, up to `--limit`;
  - each pull request's reviews, plus the merged pull request itself for who merged it.
- **Allowlisted fields:**
  - the repository's URL and default branch;
  - per pull request: number, URL, title, author, state, base branch, head commit, the
    created, merged and closed times, the merge commit and the merger;
  - per review: reviewer, state, commit and time;
  - per account: its login, its profile URL and whether it is a bot.
- **Never read:** descriptions, comments and review bodies, which are free text.
- **Tokens:** a token is optional and needs only read access. The adapter holds no
  credential that can merge (invariant 10).
- **Other forges:** Forgejo and GitLab are later readers into the same format.

**Each pull request is a source of its own.** Its collection is its URL, which stays
opaque, and its content is the record rendered as text.
- A pull request that has not changed since the last capture is skipped.
- One that has changed, through a new review or a merge, is mapped in an extraction run.
  Closing the run retracts what the newer record no longer says, such as a dismissed
  approval.
- A capture that stops at `--limit` retracts nothing about older pull requests.

**What is written.** Observed claims, each citing its pull request's record:
- **The pull request:** an Event of kind `pull_request`, keyed by its URL. Its author
  `participates_in` it (role `author`) and the repository does too (role `target`).
- **Approvals:**
  - `approved_by` from the pull request to each reviewer whose latest decisive review
    approves, judged at the merge for merged pull requests and at the capture for open
    ones.
  - The edge's `props.commit` is the reviewed commit, and it is valid from the review's
    time.
  - Requested changes and comments stay in the source; they are not edges.
- **The merge:** the merger `participates_in` the pull request (role `merger`), and the
  pull request `causes` the merge commit's `change`. That is the same node the git history
  writes, keyed by commit.

These are `approved_by` edges between an event and an agent: ordinary facts (ADR 0029).
The adapter never asserts anything on a kernel proposal. A forge's record is evidence of a
decision, never the decision.

**People.**
- **Accounts:** a forge account is an Agent keyed by `account`, its profile URL. Bots are
  `machine`; everyone else is `human`, and human agents are sealed (ADR 0022).
- **Subjects:** a pull request's source lists its people (author, reviewers, merger) as
  subjects. Erasing one of them makes the record and the claims citing it unreadable.
- **Shared kit:** sources gain `subjects`, listed as node keys. `apply` creates those agents
  before it ingests the source.
- **Identity:** a forge account and a person signed in by email (ADR 0030) are different
  agents until evidence links them with `same_as`.

**Deployments know their change.**
- The Docker capture keeps one more image label, `org.opencontainers.image.revision`: a
  commit hash, which holds no secret.
- `observe` then records the change as `part_of` the deployment.
- This stack's own images carry the label: `make` sets `WMK_REVISION` to the checked-out
  commit and the compose file passes it as a build label.

**The audit is a read.** `wmk-software forge audit REPO_URL` reads through `query_graph`
and judges in the adapter, deterministically. Each first-parent change of the repository
is one of:
- **decided:** an approved kernel proposal names its pull request. Its `props.change` is
  `{"kind": "pull_request", "url": ..., "head"?: ...}`, and a given `head` must be the
  merged head.
- **reviewed:** it was merged through a pull request that a human other than its author
  approved before the merge, on the merged head.
- **stale:** the only approval was for an earlier commit, by the forge or by a proposal.
- **unreviewed:** it was merged through a pull request that had no approval.
- **no pull request known:** no captured pull request merged it. It was pushed directly,
  rebased, or merged before the capture began.

A deployment is **deployed without approval** when its change is neither decided nor
reviewed. Deployments with no known revision are counted apart. Machines never count as
approvers. `--check` exits non-zero when anything lacks approval. The audit writes nothing:
it is a query, as ADR 0019 says, and a vital sign later.

## Consequences

- **Software pack:**
  - a `pull_request` kind;
  - identity rules for pull requests (`url`) and forge accounts (`account`);
  - the revision label in Docker captures.
- **Adapter kit:** sources with subjects.
- **The kernel does not change.**
- **This repository's record:** its pull requests are merged by their author without a
  review, so the audit reports its changes as unreviewed. That is true until branch
  protection with required reviews is on, which is the owner's setting (ADR 0019).
- **Limits:**
  - GitHub only.
  - A rebase merge shows only its last commit as merged.
  - A capture costs one request per pull request, plus one per merged pull request.
  - Self-approval across identities (a forge account and a signed-in email) is not detected
    until they are linked.
