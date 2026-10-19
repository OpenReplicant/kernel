-- T3 gate: run after modules/pack-loader/packs.sql. Raises on the first failure.
do $$
declare
  missing text;
  n int;
begin
  -- Every property the self-encoder (T6) writes is approved.
  select string_agg(p, ', ') into missing
  from unnest(array[
    'kk:fills', 'kk:moduleKind', 'owl:versionInfo', 'kk:runsAs', 'kk:protected',
    'kk:slotContract', 'kk:cardinality',
    'kkt:nodeType', 'kkt:propertyValue', 'kkt:propertyName', 'kkt:memberOf',
    'kkt:dependsOn', 'kkt:hostedOn', 'kkt:connectsTo', 'kkt:hasAffordance', 'kkt:hasRequirement',
    'kkt:substitutes', 'kkt:substitutedBy',
    'dcterms:license', 'rdfs:label']) p
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
  if n <> 33 then
    raise exception 'T3: expected 33 kk/kkt/kkp properties typed, found %', n;
  end if;
  raise notice 'ok: 33 kk/kkt/kkp properties typed';

  -- Milestone 2 vocabularies (D-47): the BBO and SOSA terms the process and devops modules write.
  select string_agg(p, ', ') into missing
  from unnest(array[
    'https://www.irit.fr/recherches/MELODI/ontologies/BBO#has_nextFlowNode',
    'https://www.irit.fr/recherches/MELODI/ontologies/BBO#has_previousFlowNode',
    'https://www.irit.fr/recherches/MELODI/ontologies/BBO#has_flowElements',
    'https://www.irit.fr/recherches/MELODI/ontologies/BBO#has_container',
    'https://www.irit.fr/recherches/MELODI/ontologies/BBO#has_resource',
    'http://www.w3.org/ns/sosa/hasSimpleResult', 'http://www.w3.org/ns/sosa/observedProperty',
    'http://www.w3.org/ns/sosa/hasFeatureOfInterest', 'http://www.w3.org/ns/sosa/madeBySensor',
    'http://www.w3.org/ns/sosa/madeByActuator', 'http://www.w3.org/ns/sosa/actsOnProperty',
    'rdfs:isDefinedBy', 'dcterms:type', 'oa:hasTarget', 'oa:bodyValue',
    'https://www.irit.fr/recherches/MELODI/ontologies/BBO#maxValue']) p
  where not kernel.is_approved_property(kernel.expand(p));
  if missing is not null then
    raise exception 'M2-T0: properties not approved: %', missing;
  end if;
  raise notice 'ok: BBO, SOSA and Web Annotation properties approved';

  select count(*) into n from kernel.claim
  where predicate = kernel.expand('skos:closeMatch')
    and subject = 'https://www.irit.fr/recherches/MELODI/ontologies/BBO#Process'
    and object_entity = kernel.expand('pplan:Plan');
  if n <> 1 then
    raise exception 'M2-T0: bbo:Process is not mapped to pplan:Plan';
  end if;
  raise notice 'ok: pack mappings installed';

  select count(distinct subject) into n from kernel.claim
  where predicate = kernel.expand('rdf:type') and object_entity = 'http://www.w3.org/ns/sosa/ObservableProperty';
  if n < 6 then
    raise exception 'M2-T1: expected the observable properties, found %', n;
  end if;
  raise notice 'ok: observable properties installed';

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
