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
changes nothing. To show it from a server with a domain name, see `deploy/public.yaml` (HTTPS by Caddy).

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
