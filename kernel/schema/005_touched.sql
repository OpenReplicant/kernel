-- Kernel v0.3 (D-112; the owner, 2026-11-21: "yes, add the touched view"): what each change set touched, for
-- modules that react to applies (the detectors, D-110) and so far read four kernel tables to find it.
--   api.touched   one row per claim a change set added, re-ranked (status), scored (belief) or verified, with the
--                 claim's subject, entity object and scope. Security invoker: a reader sees a row only for a claim
--                 it is cleared to read (the claim's row-level security, which the four tables' policies follow).
--   indexes       each of the four tables by change_set, so `where change_set = any(...)` reads few rows.
-- Redaction is not here: it is a run, not a change set (it changes current_edge and is seen there).
-- Safe to run again, on a fresh kernel (after 004) or an existing one (kk init applies it to both).
set role kk_owner;
-- Building an index holds off writes to its table; give up after a while rather than queue applies behind it.
set lock_timeout = '10s';

create index if not exists claim_change_set        on kernel.claim (change_set);
create index if not exists claim_status_change_set on kernel.claim_status (change_set);
create index if not exists belief_change_set       on kernel.belief (change_set);
create index if not exists verification_change_set on kernel.verification (change_set);

create or replace view api.touched with (security_invoker = true) as
select c.change_set, c.id as claim_id, 'added'::text as kind, c.subject, c.object_entity, c.scope
from kernel.claim c
union all
select s.change_set, c.id, 'status', c.subject, c.object_entity, c.scope
from kernel.claim_status s join kernel.claim c on c.id = s.claim_id
union all
select b.change_set, c.id, 'belief', c.subject, c.object_entity, c.scope
from kernel.belief b join kernel.claim c on c.id = b.claim_id
union all
select v.change_set, c.id, 'verification', c.subject, c.object_entity, c.scope
from kernel.verification v join kernel.claim c on c.id = v.claim_id;
revoke all on api.touched from public;
grant select on api.touched to kk_agent;

reset lock_timeout;
reset role;
