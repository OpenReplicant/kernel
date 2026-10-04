-- 30_ontology.sql
-- The ontology: four node types and the kernel edges (fixed), kinds, namespaces,
-- edge specialisations, lifecycle statuses and rules. The ontology is schema, not
-- data: it is loaded by SQL files at deploy time (this file for the core, a pack's
-- sql/ for a pack), never by kernel.write. Every entry carries a label and a
-- description because schema slicing depends on them.

SET ROLE kernel_owner;

CREATE TABLE kernel.node_types (
  name        text PRIMARY KEY CHECK (name IN ('Entity', 'Agent', 'Claim', 'Event')),
  label       text NOT NULL CHECK (label <> ''),
  description text NOT NULL CHECK (description <> ''),
  id_prefix   text NOT NULL UNIQUE
);
COMMENT ON TABLE kernel.node_types IS 'The four kernel node types. Fixed: packs never add node types.';

CREATE TABLE kernel.edge_types (
  name        text PRIMARY KEY CHECK (name IN (
                'part_of', 'instance_of', 'subtype_of', 'depends_on', 'implements', 'flows_to',
                'same_as', 'denotes',
                'about', 'supports', 'contradicts', 'supersedes', 'refines', 'assumes',
                'participates_in', 'precedes', 'causes',
                'responsible_for', 'monitors', 'approved_by', 'rejected_by', 'verified_by')),
  edge_group  text NOT NULL CHECK (edge_group IN ('structure', 'identity', 'epistemic', 'time_causation', 'governance')),
  label       text NOT NULL CHECK (label <> ''),
  description text NOT NULL CHECK (description <> ''),
  from_types  text[] NOT NULL,
  to_types    text[] NOT NULL
);
COMMENT ON TABLE kernel.edge_types IS
  'The kernel edges with their node-type domain and range. Fixed: packs specialise them in kernel.edge_kinds. violates is computed by query, never stored.';

CREATE TABLE kernel.namespaces (
  name        text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]*$'),
  label       text NOT NULL CHECK (label <> ''),
  description text NOT NULL CHECK (description <> ''),
  defined_by  text NOT NULL
);

CREATE TABLE kernel.kinds (
  name        text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]*$'),
  node_type   text NOT NULL REFERENCES kernel.node_types (name),
  label       text NOT NULL CHECK (label <> ''),
  description text NOT NULL CHECK (description <> ''),
  defined_by  text NOT NULL
);
COMMENT ON TABLE kernel.kinds IS 'Kinds of Entity, Agent and Event nodes: core kinds and pack-defined kinds.';

CREATE TABLE kernel.edge_kinds (
  -- A specialisation never shadows a kernel edge.
  name        text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]*$' AND name <> ALL (ARRAY[
                'part_of', 'instance_of', 'subtype_of', 'depends_on', 'implements', 'flows_to',
                'same_as', 'denotes', 'about', 'supports', 'contradicts', 'supersedes', 'refines', 'assumes',
                'participates_in', 'precedes', 'causes', 'responsible_for', 'monitors', 'approved_by',
                'rejected_by', 'verified_by', 'violates'])),
  edge        text NOT NULL REFERENCES kernel.edge_types (name),
  label       text NOT NULL CHECK (label <> ''),
  description text NOT NULL CHECK (description <> ''),
  defined_by  text NOT NULL
);
COMMENT ON TABLE kernel.edge_kinds IS
  'Pack specialisations of kernel edges (reads_from specialises depends_on). Stored on the kernel edge as its kind.';

CREATE TABLE kernel.statuses (
  node_type   text NOT NULL REFERENCES kernel.node_types (name),
  status      text NOT NULL CHECK (status ~ '^[a-z][a-z_]*$' AND status <> 'contested'),
  label       text NOT NULL CHECK (label <> ''),
  description text NOT NULL CHECK (description <> ''),
  is_default  boolean NOT NULL DEFAULT false,
  PRIMARY KEY (node_type, status)
);
CREATE UNIQUE INDEX statuses_one_default ON kernel.statuses (node_type) WHERE is_default;

CREATE TABLE kernel.rules (
  id          text PRIMARY KEY CHECK (id ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'),
  category    text NOT NULL CHECK (category IN ('types', 'domain_range', 'cardinality', 'time', 'identity', 'provenance')),
  namespace   text REFERENCES kernel.namespaces (name),
  params      jsonb NOT NULL CHECK (jsonb_typeof(params) = 'object'),
  label       text NOT NULL CHECK (label <> ''),
  description text NOT NULL CHECK (description <> ''),
  defined_by  text NOT NULL
);
COMMENT ON TABLE kernel.rules IS $c$Ontology rules checked inside the write transaction. params by category:
types        {"kinds": [...]}                              kinds allowed in the rule's namespace
domain_range {"edge", "kind"?, "from": {"types"?, "kinds"?}, "to": {...}}   allowed endpoints
cardinality  {"edge", "key": "from"|"to", "key_kinds": [...], "max"}       single-valued edges; windows may not overlap
time         {"check": "window_order"|"event_order"} or {"edge", "require_valid_from": true}
identity     {"node_type", "kinds": [...], "keys": [...], "patterns"?: {key: regex}}
provenance   {"edge"?, "modality"?, "min_basis"?, "requires_source"?, "basis"?}$c$;

-- Node types --------------------------------------------------------------------

INSERT INTO kernel.node_types (name, label, description, id_prefix) VALUES
  ('Entity', 'Entity', 'Anything that persists: a component, concept, data set, source, symbol, role, process, activity, gateway or pack-defined kind.', 'ent'),
  ('Agent', 'Agent', 'A human or machine actor that can assert claims, hold roles and take part in events.', 'agt'),
  ('Claim', 'Claim', 'A statement about other nodes or edges, with modality, basis, polarity, confidence and status. Goals, requirements, rules and proposals are claims with a modality.', 'clm'),
  ('Event', 'Event', 'Something that happens, with participants, a start, an end and a status.', 'evt');

-- Kernel edges --------------------------------------------------------------------

INSERT INTO kernel.edge_types (name, edge_group, label, description, from_types, to_types) VALUES
  ('part_of', 'structure', 'part of', 'The source node is a component, step or member of the target node, such as a person in a team.', '{Entity,Agent,Event}', '{Entity,Event}'),
  ('instance_of', 'structure', 'instance of', 'The source node is an instance of the target concept.', '{Entity,Agent,Event}', '{Entity}'),
  ('subtype_of', 'structure', 'subtype of', 'The source concept is a specialisation of the target concept.', '{Entity}', '{Entity}'),
  ('depends_on', 'structure', 'depends on', 'The source node needs the target node to work or exist.', '{Entity}', '{Entity}'),
  ('implements', 'structure', 'implements', 'An entity or agent fills or realises a role, such as a person who holds or takes over the approver role.', '{Entity,Agent}', '{Entity}'),
  ('flows_to', 'structure', 'flows to', 'Work, data or control passes from the source node to the target node.', '{Entity}', '{Entity}'),
  ('same_as', 'identity', 'same as', 'Two nodes denote the same thing. Links duplicates; nodes are never merged.', '{Entity,Agent,Claim,Event}', '{Entity,Agent,Claim,Event}'),
  ('denotes', 'identity', 'denotes', 'A symbol refers to the target node.', '{Entity}', '{Entity,Agent,Claim,Event}'),
  ('about', 'epistemic', 'about', 'A claim is about the target node.', '{Claim}', '{Entity,Agent,Claim,Event}'),
  ('supports', 'epistemic', 'supports', 'A claim gives evidence for another claim.', '{Claim}', '{Claim}'),
  ('contradicts', 'epistemic', 'contradicts', 'A claim conflicts with another claim.', '{Claim}', '{Claim}'),
  ('supersedes', 'epistemic', 'supersedes', 'A claim replaces another claim.', '{Claim}', '{Claim}'),
  ('refines', 'epistemic', 'refines', 'A claim makes another claim more precise.', '{Claim}', '{Claim}'),
  ('assumes', 'epistemic', 'assumes', 'A claim holds only if another claim holds.', '{Claim}', '{Claim}'),
  ('participates_in', 'time_causation', 'participates in', 'An entity or agent takes part in an event, with a role in props.role.', '{Entity,Agent}', '{Event}'),
  ('precedes', 'time_causation', 'precedes', 'The source event or step happens before the target.', '{Event,Entity}', '{Event,Entity}'),
  ('causes', 'time_causation', 'causes', 'The source event or claim brings about the target event.', '{Event,Claim}', '{Event}'),
  ('responsible_for', 'governance', 'responsible for', 'An agent or role is accountable for the target.', '{Agent,Entity}', '{Entity,Event,Claim}'),
  ('monitors', 'governance', 'monitors', 'An agent or entity watches the target.', '{Agent,Entity}', '{Entity,Agent,Event}'),
  ('approved_by', 'governance', 'approved by', 'A claim, event or entity was approved by an agent.', '{Claim,Event,Entity}', '{Agent}'),
  ('rejected_by', 'governance', 'rejected by', 'A claim, event or entity was rejected by an agent.', '{Claim,Event,Entity}', '{Agent}'),
  ('verified_by', 'governance', 'verified by', 'A claim was checked against an event, another claim or an agent.', '{Claim}', '{Event,Claim,Agent}');

-- Core namespace and kinds ----------------------------------------------------------

INSERT INTO kernel.namespaces (name, label, description, defined_by) VALUES
  ('core', 'Core', 'The default namespace. Every core and pack kind is allowed here.', 'core');

INSERT INTO kernel.kinds (name, node_type, label, description, defined_by) VALUES
  ('component', 'Entity', 'Component', 'A software or hardware system, service, tool or part of one.', 'core'),
  ('concept', 'Entity', 'Concept', 'An idea, category, topic or term of a domain.', 'core'),
  ('data', 'Entity', 'Data', 'A data set, document type, record type or business object such as an invoice.', 'core'),
  ('source', 'Entity', 'Source', 'A document, system or person treated as a source of information.', 'core'),
  ('symbol', 'Entity', 'Symbol', 'A name, code, acronym or term that denotes something else.', 'core'),
  ('role', 'Entity', 'Role', 'A position or responsibility that an agent or entity can fill, such as approver.', 'core'),
  ('process', 'Entity', 'Process', 'A repeatable sequence of activities that produces an outcome.', 'core'),
  ('activity', 'Entity', 'Activity', 'A step of work within a process.', 'core'),
  ('gateway', 'Entity', 'Gateway', 'A decision or branching point within a process.', 'core'),
  ('human', 'Agent', 'Human', 'A person.', 'core'),
  ('machine', 'Agent', 'Machine', 'A software agent, model run or configured assistant.', 'core'),
  ('occurrence', 'Event', 'Occurrence', 'Something that happened or will happen, when no more specific kind fits.', 'core'),
  ('meeting', 'Event', 'Meeting', 'A gathering of participants, such as an interview or review.', 'core'),
  ('change', 'Event', 'Change', 'A change to a system, process, organisation or role assignment.', 'core'),
  ('incident', 'Event', 'Incident', 'An unplanned disruption or failure.', 'core');

INSERT INTO kernel.statuses (node_type, status, label, description, is_default) VALUES
  ('Entity', 'active', 'Active', 'The entity exists and is in use.', true),
  ('Entity', 'retired', 'Retired', 'The entity no longer exists or is no longer in use.', false),
  ('Agent', 'active', 'Active', 'The agent can act and assert.', true),
  ('Agent', 'inactive', 'Inactive', 'The agent no longer acts.', false),
  ('Claim', 'open', 'Open', 'The claim is stated and awaits no decision, or awaits one.', true),
  ('Claim', 'approved', 'Approved', 'A proposal or normative claim was approved.', false),
  ('Claim', 'rejected', 'Rejected', 'A proposal or normative claim was rejected.', false),
  ('Claim', 'withdrawn', 'Withdrawn', 'The claim was withdrawn by whoever made it.', false),
  ('Event', 'planned', 'Planned', 'The event is expected to happen.', false),
  ('Event', 'ongoing', 'Ongoing', 'The event has started and not ended.', false),
  ('Event', 'completed', 'Completed', 'The event has ended.', true),
  ('Event', 'cancelled', 'Cancelled', 'The event will not happen.', false);

-- Core rules ------------------------------------------------------------------------

INSERT INTO kernel.rules (id, category, namespace, params, label, description, defined_by) VALUES
  ('core.window_order', 'time', NULL, '{"check": "window_order"}',
   'Validity windows are ordered', 'valid_from must be earlier than valid_to.', 'core'),
  ('core.event_order', 'time', NULL, '{"check": "event_order"}',
   'Events end after they start', 'An event''s end may not be earlier than its start.', 'core'),
  ('core.implements_role', 'domain_range', NULL,
   '{"edge": "implements", "to": {"kinds": ["role"]}}',
   'Only roles are implemented', 'implements must point at an entity of kind role.', 'core'),
  ('core.denotes_symbol', 'domain_range', NULL,
   '{"edge": "denotes", "from": {"kinds": ["symbol"]}}',
   'Only symbols denote', 'denotes must start at an entity of kind symbol.', 'core'),
  ('core.same_as_same_type', 'domain_range', NULL,
   '{"edge": "same_as", "same_type": true}',
   'same_as links nodes of one type', 'same_as may only link two different nodes of the same node type.', 'core'),
  ('core.reported_needs_source', 'provenance', NULL,
   '{"basis": "reported", "requires_source": true}',
   'Reported claims cite a source', 'A claim with basis reported must name the chunk it was read from.', 'core'),
  ('core.human_email', 'identity', NULL,
   '{"node_type": "Agent", "kinds": ["human"], "keys": ["email"], "patterns": {"email": "^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$"}}',
   'Email identifies a person', 'Two human agents with the same email are the same person.', 'core'),
  ('core.machine_profile', 'identity', NULL,
   '{"node_type": "Agent", "kinds": ["machine"], "keys": ["profile"]}',
   'Profile identifies a machine agent', 'Two machine agents with the same profile name are the same configuration.', 'core');
