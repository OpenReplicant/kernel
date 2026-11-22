# The process demo

A twenty-minute walkthrough for someone who runs processes: what was documented, what actually
happens, which differences matter, and a process model that learns from what people decide. Every
number on screen is a claim you can click to see where it came from.

## Set up (once, five minutes)

Needs Docker (or Podman) and Python 3.12+.

```sh
git clone https://github.com/OpenReplicant/kernel && cd kernel
pip install -e .
kk demo            # the bundled order-to-cash process: 40 orders, offline
# or: kk demo --bpi  # BPI Challenge 2019: 1.6 M events from a real purchasing process (728 MB download, ~5 min)
```

`kk demo` starts the stack, loads the event log, the documented BPMN model and the written procedure,
and prints the address (http://127.0.0.1:8080/ui/) with the owner's name and password. Running it again
changes nothing. `kk demo --interview` is the other demo: a scripted interview about a purchasing team plays
in the Map tab's chat pane and the organisation chart and the process fill as each answer is recorded
(`--fast` plays it at once). Correct a name or an edge in place, confirm what the interviewer proposed, or type an
answer (it is recorded; claims are taken from it when a model is set with `KK_EXTRACT_URL`). To show it from a server with a domain name, see `deploy/public.yaml` (HTTPS by Caddy).

## The business distribution

A distribution is a preset bundle of modules, packs, skills and views that does one job (`kk distribution list`).
The business suite builds org and process maps from a conversation, with mined logs beside them:

```sh
kk demo --distribution business   # its own stack (compose project kk-business, UI on its own port); plays the interview
kk up --distribution business     # the same stack for your own project folder, without the demo data
```

Sign in as owner (printed) and open the Map tab: the maps fill as the interview plays. The UI lists only this
distribution's views. `process-maps` is the order-to-cash demo above as a distribution. Running either again
changes nothing.

## Run a distribution from its bundle

Without cloning or building: each distribution has a folder of published images and a compose file
(`distributions/<name>/bundle/`, or `kk distribution bundle NAME --out DIR` writes a fresh one). Copy that folder anywhere and:

```sh
cp .env.example .env       # fill in the three secrets (openssl rand -hex 24, 24 and 32; the file says which)
podman compose up -d       # docker compose works the same
podman compose run --rm init kk demo --distribution business --no-stack   # loads the demo, prints the owner's sign-in
```

Then open `http://127.0.0.1:<port>/ui/` (the bundle's README gives the port) and sign in as `owner`. The images are
`ghcr.io/openreplicant/kernel` and `ghcr.io/openreplicant/kernel-postgres`, tagged `main`, the commit and each release;
pin a release in `.env` (`KK_IMAGE`, `KK_DB_IMAGE`) for anything you keep. Bundles use the same ports as
`kk up --distribution`, so several run side by side. `devops` has the kernel's side only (its observer and actuator
are module containers installed from the repository) and `papers` has no scripted demo; their READMEs say what to run
instead. D-120.

## The walkthrough

**1. Sign in** as `owner` with the printed password. Everyone signs in as themselves, and the database
enforces what each person may see and approve. No model decides that.

**2. Processes → the map.** Dashed lines are documented flows that never happened. Teal lines are what
happened as documented. Blue lines are what happened outside the documentation, with their width and
number giving the case count. Three sources meet on one map: the event log (observed), the BPMN model
(documented) and the written procedure (extracted, with the sentence it came from). Click any line to
see its claims, each with its evidence type and belief.

**3. Discrepancies, each with its evidence.** Each item is one sentence a reviewer can act on, for
example "3 case(s) went from Ship goods to Receive payment without Send invoice". Below it are the claims
it was derived from: the observed counts and the documented flow it contradicts. The time-limit item is
"The wait from Ship goods to Send invoice reached 122 h; the documented limit is 48 h". The 48 h comes
from the procedure's sentence ("…within two days of shipping"), and the quote verifier checked that the
sentence is in the stored file. On BPI 2019 the list is 35 items, not 495. Paths that under 1% of cases
took are grouped into one "rare paths" item (D-75).

**4. Confirm the real deviations.** Confirm (or reject) each item. A person decides, and the decision
is recorded with their name.

**5. Learned from practice.** For the confirmed deviations that many cases take, the process learner
proposes making them part of the documented process: "Practice: 3 of 40 cases (8%) went from Send
invoice to Ship goods… Approve to make Send invoice → Ship goods part of it." Approve one. The map
updates, and on the next pass conformance retracts the discrepancy, because practice and documentation
now agree. Reject one, and it stays a deviation and is not proposed again. This is the Learn step of
the monitor–analyse–plan–execute loop, and it stays under people's control.

**6. Download BPMN.** The documented process, with the learned paths included, as standard BPMN 2.0.
It opens in BPMN editors (checked with bpmn.io's own parser).

**7. Ask the agent.** Open the demo project (`claude` in `~/kk-demo`) and ask: *"Which deviations
from the order-to-cash process were confirmed, and in how many cases?"* The answer cites claim ids with
their belief, so any number in it can be traced back in the UI (Search → explain).

**8. Under the hood (optional).** Change sets show every write: who submitted it, which run made it,
whether it was validated, reviewed and applied. Nothing is deleted. A retracted discrepancy keeps its
history (Search → explain).

## What to say it is for

- **Process compliance and audit:** documented vs actual, each finding traceable to the records behind
  it, and decisions recorded with who made them.
- **Process improvement:** the deviations worth discussing, ranked by how many cases they touch, and a
  model that absorbs the agreed changes instead of going stale.
- **Agents on your processes:** the same claims serve people and AI agents. Agents answer from them with
  citations, and anything an agent proposes goes through the same review.

## Resetting

`docker compose -f deploy/compose.yaml --env-file deploy/.env down -v` removes the demo stack and its
data. `kk demo` builds it again.
