# Detectors

Reacts to what the kernel applied (M6-T0, D-110; the design is in `docs/decisions.md`). This file is what an
author of a detector needs, and what the adversarial review of D-110 added.

## Writing a detector

A detector is a SQL query (or a Python `find(conn, anchors)`) that returns, per match: `key`, `nodes` (text[],
every entity whose change could change the match, also those under a negated pattern), `claims` (uuid[], what
the match was made from) and optionally `data` (jsonb). The query gets `%(anchors)s` (text[] of the entities
the new change sets changed, or null for a full look). The query goes through psycopg: write a literal `%` as
`%%` (`like 'urn:x:%%'`).

Registered in a YAML file (`KK_DETECTORS_FILE`) with `name`, `owner` (the db role it runs as), `scope` (where its
events are written) and `anchored`.

## Clearance (B1)

The key, the nodes and the data of a match are content. They are copied into claims in the detector's event
scope, and everyone who reads that scope reads them. They can carry what the owner read, also through a negation
(a match that exists because a claim does not). So the engine refuses a detector, when it is registered
(`Engine.validate`, printed at start) and on every pass, unless every reader of its event scope can also read
every scope its owner can read. A detector whose owner is cleared for a secret scope writes its events to that
scope (or one no more readable). The check uses `kernel.scope_grant` (read only), counting a role as able to read
a scope when it is a member of a role granted it, as `can_read` does. Owners named `person_*`, `agent_*` or
`kk_*`, superusers and roles that bypass row-level security are refused.

## Rules for authors

- Use claim ids, not values, in `key`. Redaction of a claim does not reach a copy of its value in a match's
  label. The engine deprecates the label (the claim that carries the key) when the match dissolves (B8), but a
  match that holds keeps its key readable. A key such as `<claim id>` or `<subject>|<object>` leaks nothing a
  redaction would need to remove.
- A detector name is unique in a kernel. Events are claims attributed to the engine's agent, and only those
  count as stored state (B2): another agent's claims about a detector's matches or events are ignored. The
  engine refuses two detectors with one name.
- A detector's query runs on its own connection, as its owner, in a read-only transaction with a statement
  timeout (SQL and Python finds alike). Switching role inside the query (`set_config('role', ...)`) is
  detected and the results are dropped (B3). Each owner has a connection of its own, never the module's, and
  it is reset (`discard all`, then `set role`) before every use.

## What a pass records

- The cursor moves only after the change sets of the run are applied. A change set the kernel marks invalid,
  rejected or in conflict is reported on the next pass as a failed run (`info.reported` names the run) and
  its range is looked at again, under a new idempotency key (B5).
- Events are split into change sets of at most `KK_DETECTORS_CHUNK` matches (default 200) (B9). With anchors,
  only stored matches that name an anchor, or that the query found now, are read.
- A change set key that another agent took first (the kernel answers with their change set, or refuses it as a
  duplicate) is moved past with a key bound to the run (B7).
- A detector that fails is tried again after `KK_DETECTORS_BACKOFF_S` seconds (default 5), doubled for each
  failure in a row up to 300 (B10); a pass reports `backoff` for it meanwhile.
- A belief or verification that arrives in its own change set anchors its claim, like a new claim or a status
  change (B4).

## Limits

- A query that sets its own `statement_timeout` with `set_config(..., true)` inside the statement can outrun it.
- Level-triggered: a match that forms and dissolves between two passes is never seen.
