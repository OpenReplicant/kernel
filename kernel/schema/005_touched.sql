-- Kernel v0.3 (D-112; the owner, 2026-11-21: "yes, add the touched view"): what each change set touched, for
-- modules that react to applies (the detectors, D-110) and so far read four kernel tables to find it.
--   api.touched   one row per claim a change set added, re-ranked (status), scored (belief) or verified, with the
--                 claim's subject, entity object and scope. Security invoker: a reader sees a row only for a claim
--                 it is cleared to read (the claim's row-level security, which the four tables' policies follow).
--   indexes       each of the four tables by change_set, so `where change_set = any(...)` reads few rows.
-- Redaction is not here: it is a run, not a change set (it changes current_edge and is seen there).
-- Safe to run again, on a fresh kernel (after 004) or an existing one (kk init applies it to both).
set role kk_owner;
-- `kk init` runs this file as one transaction, so each index's lock is held to the end. Take the apply lock
-- first (kernel.serialize(), D-109): an apply, the only writer of these four tables but redaction, takes it before
-- touching them, so no apply holds one of them while waiting for this file and none deadlocks with it; applies
-- that start meanwhile wait until the indexes are built (seconds, even on a large kernel), and a second `kk init`
-- waits and then finds them there. A redaction takes the lock just after updating its claim, so one that runs at
-- that very moment may be stopped as a deadlock; it changed nothing and can be run again. If an apply holds the lock for longer than lock_timeout, this file gives up
-- whole and `kk init` can be run again.
set lock_timeout = '10s';
select kernel.serialize();

create index if not exists claim_change_set        on kernel.claim (change_set);
create index if not exists claim_status_change_set on kernel.claim_status (change_set);
create index if not exists belief_change_set       on kernel.belief (change_set);
create index if not exists verification_change_set on kernel.verification (change_set);

-- One index scan per table, then the claim joined once: a join on change_set (to api.applied, say) reaches the
-- indexes through the union (with a join inside each branch, it scanned them whole; found by D-112's review).
-- A claim scored twice in one change set shows twice: read `distinct` where that matters.
create or replace view api.touched with (security_invoker = true) as
select u.change_set, c.id as claim_id, u.kind, c.subject, c.object_entity, c.scope
from (select change_set, id as claim_id, 'added'::text as kind from kernel.claim
      union all select change_set, claim_id, 'status' from kernel.claim_status
      union all select change_set, claim_id, 'belief' from kernel.belief
      union all select change_set, claim_id, 'verification' from kernel.verification) u
join kernel.claim c on c.id = u.claim_id;
revoke all on api.touched from public;
grant select on api.touched to kk_agent;

reset lock_timeout;
reset role;
