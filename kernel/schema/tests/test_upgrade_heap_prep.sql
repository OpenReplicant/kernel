-- D-111 review: before 004 on an existing kernel, make claim_status's stored order differ from its written order.
-- A rolled-back bulk insert leaves dead rows; vacuum frees their space; a later status row then lands before an
-- earlier one. 004 must number them in the order they were written. test_upgrade_heap_check.sql checks it after 004.
\set QUIET on
\set ON_ERROR_STOP on
\o /dev/null
create table public.t_heap as
  select c.id as claim, c.change_set from kernel.claim c where c.redacted_at is null order by c.recorded_at limit 1;
begin;
insert into kernel.claim_status (claim_id, status, reason, change_set)
  select claim, 'normal', 'review', change_set from public.t_heap, generate_series(1, 3000);
rollback;
insert into kernel.claim_status (claim_id, status, reason, change_set)
  select claim, 'deprecated', 'retracted', change_set from public.t_heap;          -- written first
vacuum kernel.claim_status;
select pg_sleep(0.05);
insert into kernel.claim_status (claim_id, status, reason, change_set)
  select claim, 'preferred', 'review', change_set from public.t_heap;              -- written last, stored first
select public.check((select ctid from kernel.claim_status where claim_id = (select claim from public.t_heap) and status = 'preferred')
                    < (select ctid from kernel.claim_status where claim_id = (select claim from public.t_heap) and status = 'deprecated'),
                    'precondition: the later status row is stored before the earlier one');
select public.check(kernel.current_status((select claim from public.t_heap)) = 'preferred', 'precondition: before 004 it is preferred');
\o
