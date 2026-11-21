-- After 004 (and 003, 004 again) on the kernel test_upgrade_heap_prep.sql prepared.
\set QUIET on
\set ON_ERROR_STOP on
\o /dev/null
select public.check(kernel.current_status((select claim from public.t_heap)) = 'preferred',
                    '004 numbers existing status rows in the order they were written, not the order they are stored');
select public.check((select s1.seq < s2.seq from kernel.claim_status s1, kernel.claim_status s2
                     where s1.claim_id = (select claim from public.t_heap) and s1.status = 'deprecated'
                       and s2.claim_id = s1.claim_id and s2.status = 'preferred'), 'the earlier row has the lower number');
select public.check((select count(*) = count(distinct seq) and min(seq) >= 1 from kernel.claim_status), 'numbers are distinct');
drop table public.t_heap;
\o
select 'UPGRADE ORDER TESTS PASSED' as result;
