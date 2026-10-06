# 0030. Signing in to decide: the explorer's Proposals page

Date: 2026-10-06 · Status: accepted (amends ADR 0016: the explorer reads, and a signed-in
person decides)

## Context

ADR 0029 built `kernel.decide`. It runs only as `kernel_approver`, for the person a
verified token names in `request.jwt.claims`. Something still has to verify the token and
take that role. Nothing does yet, so only an operator's database session can decide.

The explorer already reaches the database through PostgREST as the reader role
(ADR 0016). PostgREST can verify a JSON Web Token, switch to the role it names, and pass
its claims to the database.

## Decision

**PostgREST verifies, the database decides.**
- PostgREST logs in as `wmk_api`, a `NOINHERIT` login that is a member of `kernel_reader`
  and `kernel_approver`.
  - Without a token it switches to the reader.
  - With a token whose `role` is `kernel_approver`, it switches to the approver and puts
    the token's claims in `request.jwt.claims`.
- **Writes:** no gateway login is a member of `kernel_approver`, and the approver can
  execute only `kernel.decide`.
- **Transactions:** they still roll back by default. A decision asks to commit
  (`Prefer: tx=commit`, under `db-tx-end = rollback-allow-override`), so a reader request
  never commits anything.

**Tokens are signed with a secret the agents never see.**
- **The secret:** `WMK_JWT_SECRET`, at least 32 characters. It is set only on the
  explorer's API service. The gateway never has it, and the stack has no default: without
  it, PostgREST verifies nothing and the explorer is read-only, as before.
- **Local tokens:**
  - An operator mints a token for one person with `make token EMAIL=...`
    (`kernel/token.py`, HS256 with the standard library). It carries `role`, `email`,
    `name` and an expiry.
  - The person pastes it into the explorer's sign-in box. It is kept in the tab's
    `sessionStorage`, so it goes when the tab closes.
  - Anyone who can read the secret can mint a token for anyone. So the secret belongs to
    the operator's environment, never to an agent's, and a static token stands for one
    person on one stack.
- **Identity providers come next.** With Forgejo as the OpenID Connect provider,
  PostgREST verifies the provider's tokens against its published keys. Providers do not
  emit `role: kernel_approver`, so a small exchange is needed: an auth proxy such as
  oauth2-proxy (MIT), or a provider-side claim mapper read through PostgREST's role claim
  key. That is a later step; the database side does not change.

**The Proposals page** lists proposals from a read view, `kernel.proposals_view`, which
holds for each one:
- its text and what it changes (`props.change`);
- what it is about;
- its status and who proposed it;
- the approvals it has and needs, and whether it touches an instrument;
- the claims that support or contradict it, and the events that verified it.

Signed in, a person can:
- approve or reject an open proposal, with a reason;
- protect a node as an instrument from that node's page.

Refusals come back as the kernel's problem documents and are shown as they are: an
approver's own proposal, a system they are part of, a second approval by the same person.
`kernel.signed_in()` tells the page who the token names and which agent that is.

**The email is the identity.** On a person's first decision their agent registers itself,
keyed by the token's email. A human of the same name already in the graph without that
email may be someone else, so the new agent is recorded as distinct from them instead of
being refused as a duplicate; evidence can link the two later.

**The forge stays the gate for code.** This repository gets a CODEOWNERS file for the
instrument paths: kernel SQL, evals, pack rules, `.github/` and profiles. Branch
protection with required review is the owner's setting to turn on (ADR 0019).

## Consequences

- A person can approve in a browser, and no agent can. The test for that is the explorer's
  smoke check: it signs in with a minted token and approves the seeded proposal through
  the page, while the same request without a token is refused.
- CI generates a throwaway secret for that check.
- The explorer is no longer purely read-only. Its one write is `kernel.decide`, with a
  token.
- Kernel 0.6.1: the proposals view, `kernel.signed_in()` and the registration above are
  additive; the log and the write path do not change.
- Sign-in through an identity provider and the forge adapter (ADR 0029's third step)
  remain to be built.
