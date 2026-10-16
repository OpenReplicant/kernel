-- Knowledge Kernel v0 — bootstrap data
-- Prefixes, settings, the kernel agent, the two default scopes, and the W3C core properties
-- every pack depends on. Vocabulary packs (ontology/*.ttl) are installed afterwards by the
-- pack loader, through kernel.install_ops, never by direct inserts.

set role kk_owner;

insert into kernel.prefix(prefix, namespace) values
  ('rdf',     'http://www.w3.org/1999/02/22-rdf-syntax-ns#'),
  ('rdfs',    'http://www.w3.org/2000/01/rdf-schema#'),
  ('owl',     'http://www.w3.org/2002/07/owl#'),
  ('xsd',     'http://www.w3.org/2001/XMLSchema#'),
  ('skos',    'http://www.w3.org/2004/02/skos/core#'),
  ('dcterms', 'http://purl.org/dc/terms/'),
  ('prov',    'http://www.w3.org/ns/prov#'),
  ('pplan',   'http://purl.org/net/p-plan#'),
  ('oa',      'http://www.w3.org/ns/oa#'),
  ('sh',      'http://www.w3.org/ns/shacl#'),
  ('td',      'https://www.w3.org/2019/wot/td#'),
  ('kk',      'https://w3id.org/knowledge-kernel/ns#'),
  ('kkt',     'https://w3id.org/knowledge-kernel/tosca#'),
  ('kkp',     'https://w3id.org/knowledge-kernel/profile#');
-- The kk namespaces are placeholders until a w3id.org redirect is registered (docs/decisions.md D-14).

insert into kernel.setting(key, value) values
  ('instance_base',       '"urn:kk:"'),
  ('active_belief_model', '"urn:kk:module:belief-corroboration-v0"'),
  -- claims with these predicates always take the review lane
  ('review_predicates',   '["rdfs:subClassOf","rdfs:subPropertyOf","rdfs:domain","rdfs:range",
                            "owl:equivalentClass","owl:equivalentProperty",
                            "skos:exactMatch","skos:closeMatch","skos:broadMatch","skos:narrowMatch",
                            "skos:relatedMatch"]'),
  -- rdf:type claims with these objects always take the review lane
  ('review_types',        '["rdf:Property","owl:ObjectProperty","owl:DatatypeProperty",
                            "owl:AnnotationProperty","rdfs:Class","owl:Class",
                            "kk:Module","kk:Chain","kkp:AgentProfile","kkt:NodeType"]');

insert into kernel.agent(iri, db_role, kind, label) values
  ('urn:kk:agent:kernel', 'kk_owner', 'software', 'Knowledge Kernel migrations');

insert into kernel.scope(iri, label) values
  ('urn:kk:scope:vocab',   'Vocabulary: terms, shapes, mappings, module and slot descriptions'),
  ('urn:kk:scope:default', 'Default working scope');

insert into kernel.scope_grant(scope, db_role, can_read, can_write) values
  ('urn:kk:scope:vocab',   'kk_agent', true, true),   -- typing/mapping claims are forced to review anyway
  ('urn:kk:scope:default', 'kk_agent', true, true);

-- Approve the core properties by typing them, through the normal apply path.
select kernel.install_ops(
  'bootstrap:core-properties:v0',
  'urn:kk:scope:vocab',
  (select jsonb_agg(jsonb_build_object(
      'op', 'add_claim',
      'subject', p.term,
      'predicate', 'rdf:type',
      'object', jsonb_build_object('entity', p.kind),
      'evidence_type', 'deterministic',
      'references', jsonb_build_array(jsonb_build_object('source_uri', p.spec))))
   from (values
     ('rdf:type',              'rdf:Property',          'https://www.w3.org/TR/rdf12-schema/'),
     ('rdfs:label',            'owl:AnnotationProperty','https://www.w3.org/TR/rdf12-schema/'),
     ('rdfs:comment',          'owl:AnnotationProperty','https://www.w3.org/TR/rdf12-schema/'),
     ('rdfs:subClassOf',       'rdf:Property',          'https://www.w3.org/TR/rdf12-schema/'),
     ('rdfs:subPropertyOf',    'rdf:Property',          'https://www.w3.org/TR/rdf12-schema/'),
     ('rdfs:domain',           'rdf:Property',          'https://www.w3.org/TR/rdf12-schema/'),
     ('rdfs:range',            'rdf:Property',          'https://www.w3.org/TR/rdf12-schema/'),
     ('rdfs:seeAlso',          'rdf:Property',          'https://www.w3.org/TR/rdf12-schema/'),
     ('owl:equivalentClass',   'rdf:Property',          'https://www.w3.org/TR/owl2-syntax/'),
     ('owl:equivalentProperty','rdf:Property',          'https://www.w3.org/TR/owl2-syntax/'),
     ('owl:deprecated',        'owl:AnnotationProperty','https://www.w3.org/TR/owl2-syntax/'),
     ('skos:prefLabel',        'owl:AnnotationProperty','https://www.w3.org/TR/skos-reference/'),
     ('skos:altLabel',         'owl:AnnotationProperty','https://www.w3.org/TR/skos-reference/'),
     ('skos:definition',       'owl:AnnotationProperty','https://www.w3.org/TR/skos-reference/'),
     ('skos:exactMatch',       'owl:ObjectProperty',    'https://www.w3.org/TR/skos-reference/'),
     ('skos:closeMatch',       'owl:ObjectProperty',    'https://www.w3.org/TR/skos-reference/'),
     ('skos:broadMatch',       'owl:ObjectProperty',    'https://www.w3.org/TR/skos-reference/'),
     ('skos:narrowMatch',      'owl:ObjectProperty',    'https://www.w3.org/TR/skos-reference/'),
     ('skos:relatedMatch',     'owl:ObjectProperty',    'https://www.w3.org/TR/skos-reference/'),
     ('skos:broader',          'owl:ObjectProperty',    'https://www.w3.org/TR/skos-reference/'),
     ('skos:inScheme',         'owl:ObjectProperty',    'https://www.w3.org/TR/skos-reference/'),
     ('dcterms:isPartOf',      'rdf:Property',          'https://www.dublincore.org/specifications/dublin-core/dcmi-terms/'),
     ('dcterms:hasPart',       'rdf:Property',          'https://www.dublincore.org/specifications/dublin-core/dcmi-terms/'),
     ('dcterms:isReplacedBy',  'rdf:Property',          'https://www.dublincore.org/specifications/dublin-core/dcmi-terms/'),
     ('dcterms:source',        'rdf:Property',          'https://www.dublincore.org/specifications/dublin-core/dcmi-terms/'),
     ('dcterms:license',       'rdf:Property',          'https://www.dublincore.org/specifications/dublin-core/dcmi-terms/'),
     ('dcterms:hasVersion',    'rdf:Property',          'https://www.dublincore.org/specifications/dublin-core/dcmi-terms/'),
     ('pplan:isStepOfPlan',    'owl:ObjectProperty',    'https://www.opmw.org/model/p-plan/'),
     ('pplan:isPrecededBy',    'owl:ObjectProperty',    'https://www.opmw.org/model/p-plan/'),
     ('pplan:hasInputVar',     'owl:ObjectProperty',    'https://www.opmw.org/model/p-plan/'),
     ('pplan:hasOutputVar',    'owl:ObjectProperty',    'https://www.opmw.org/model/p-plan/'),
     ('pplan:isSubPlanOfPlan', 'owl:ObjectProperty',    'https://www.opmw.org/model/p-plan/')
   ) as p(term, kind, spec)));

reset role;
