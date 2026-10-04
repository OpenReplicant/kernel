-- bpm-reference: a toy business-process pack for CI fixtures and the MVP demo.
-- Phase 1 has no pack installer: this file is applied after kernel/sql by the same
-- deploy step (db/initdb, kernel.admin). It only adds ontology entries; every one
-- carries a label and a description.

SET ROLE kernel_owner;

INSERT INTO kernel.namespaces (name, label, description, defined_by) VALUES
  ('bpm', 'Business processes', 'How work flows through an organisation: processes, their activities and gateways, the roles that perform them and the units that own them.', 'bpm-reference');

INSERT INTO kernel.kinds (name, node_type, label, description, defined_by) VALUES
  ('org_unit', 'Entity', 'Organisational unit', 'A department, team or other unit of an organisation that owns processes or roles.', 'bpm-reference');

INSERT INTO kernel.edge_kinds (name, edge, label, description, defined_by) VALUES
  ('reads_from', 'depends_on', 'reads from', 'An activity reads a data set or business object, such as an invoice.', 'bpm-reference'),
  ('uses', 'depends_on', 'uses', 'An activity is carried out in a system or tool.', 'bpm-reference');

INSERT INTO kernel.rules (id, category, namespace, params, label, description, defined_by) VALUES
  ('bpm.allowed_kinds', 'types', 'bpm',
   '{"kinds": ["process", "activity", "gateway", "role", "org_unit"]}',
   'Business-process kinds', 'The bpm namespace allows processes, activities, gateways, roles and organisational units.',
   'bpm-reference'),
  ('bpm.flows_between_steps', 'domain_range', 'bpm',
   '{"edge": "flows_to", "from": {"kinds": ["activity", "gateway"]}, "to": {"kinds": ["activity", "gateway"]}}',
   'Flows connect steps', 'flows_to connects activities and gateways.', 'bpm-reference'),
  ('bpm.reads_from_data', 'domain_range', 'bpm',
   '{"edge": "depends_on", "kind": "reads_from", "from": {"kinds": ["activity"]}, "to": {"kinds": ["data"]}}',
   'Activities read data', 'reads_from goes from an activity to a data entity.', 'bpm-reference'),
  ('bpm.uses_component', 'domain_range', 'bpm',
   '{"edge": "depends_on", "kind": "uses", "from": {"kinds": ["activity"]}, "to": {"kinds": ["component"]}}',
   'Activities use systems', 'uses goes from an activity to a component.', 'bpm-reference'),
  ('bpm.approver_cardinality', 'cardinality', 'bpm',
   '{"edge": "implements", "key": "to", "key_kinds": ["role"], "max": 1}',
   'One holder per role at a time', 'A bpm role has one holder at a time: implements windows into the same role may not overlap.',
   'bpm-reference'),
  ('bpm.approval_needs_report', 'provenance', NULL,
   '{"edge": "approved_by", "min_basis": "reported"}',
   'Approvals are reported or observed', 'An approval may not be inferred: approved_by needs basis reported or observed.',
   'bpm-reference');

RESET ROLE;
