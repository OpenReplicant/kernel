-- T3 gate: run after modules/pack-loader/packs.sql. Raises on the first failure.
do $$
declare
  missing text;
  n int;
begin
  -- Every property the self-encoder (T6) writes is approved.
  select string_agg(p, ', ') into missing
  from unnest(array[
    'kk:fills', 'kk:moduleKind', 'kk:version', 'kk:runsAs', 'kk:protected',
    'kk:stepType', 'kk:stepConfig', 'kk:next', 'kk:onLabel', 'kk:trigger',
    'kk:runnable', 'kk:notRunnableBecause', 'kk:deployedHash', 'kk:slotContract', 'kk:cardinality',
    'kkt:nodeType', 'kkt:propertyValue', 'kkt:propertyName', 'kkt:memberOf',
    'kkt:dependsOn', 'kkt:hostedOn', 'kkt:connectsTo', 'kkt:hasAffordance', 'kkt:hasRequirement',
    'kkt:substitutes', 'kkt:substitutedBy',
    'dcterms:license', 'rdfs:label', 'pplan:isStepOfPlan']) p
  where not kernel.is_approved_property(kernel.expand(p));
  if missing is not null then
    raise exception 'T3: properties not approved: %', missing;
  end if;
  raise notice 'ok: every self-encoder property is approved';

  -- Every kk/kkt/kkp property typed in a pack is approved.
  select count(*) into n from kernel.claim c
  where c.predicate = kernel.expand('rdf:type')
    and c.object_entity in (kernel.expand('rdf:Property'), kernel.expand('owl:ObjectProperty'),
                            kernel.expand('owl:DatatypeProperty'), kernel.expand('owl:AnnotationProperty'))
    and c.subject like 'https://w3id.org/knowledge-kernel/%';
  if n <> 61 then
    raise exception 'T3: expected 61 kk/kkt/kkp properties typed, found %', n;
  end if;
  raise notice 'ok: 61 kk/kkt/kkp properties typed';

  select count(distinct subject) into n from kernel.claim
  where predicate = kernel.expand('rdf:type') and object_entity = kernel.expand('kk:Slot');
  if n <> 42 then
    raise exception 'T3: expected 42 slot entities, found %', n;
  end if;
  raise notice 'ok: 42 slot entities';

  select count(*) into n from kernel.claim c
  where c.change_set in (select id from kernel.change_set where idempotency_key like 'pack:%')
    and not exists (select 1 from kernel.claim_reference r where r.claim_id = c.id);
  if n <> 0 then
    raise exception 'T3: % pack claims without a reference', n;
  end if;
  raise notice 'ok: every pack claim is referenced';

  select count(*) into n from kernel.claim c
  where c.change_set in (select id from kernel.change_set where idempotency_key like 'pack:%')
    and c.attributed_to <> 'urn:kk:agent:pack-loader';
  if n <> 0 then
    raise exception 'T3: % pack claims not attributed to the pack loader', n;
  end if;
  raise notice 'ok: pack claims attributed to the pack loader';
end $$;
select 'T3 PACK CHECKS PASSED' as result;
