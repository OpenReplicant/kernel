-- research: the research pack's ontology. Papers, the methods, data and measures they
-- study, who wrote them, and the findings, hypotheses and open questions they report.
-- Findings are Claim nodes, one per paper and result; agreement and disagreement between
-- them are supports and contradicts edges (ADR 0013). Applied after kernel/sql by the same
-- deploy step as every pack (there is no pack installer yet). Every entry carries a label
-- and a description because schema slicing depends on them.

SET ROLE kernel_owner;

INSERT INTO kernel.namespaces (name, label, description, defined_by) VALUES
  ('research', 'Research', 'Scholarly research: papers, the methods, data and measures they study, their authors, and the findings, hypotheses and open questions they report.', 'research');

INSERT INTO kernel.kinds (name, node_type, label, description, defined_by) VALUES
  ('paper', 'Entity', 'Paper', 'A scholarly publication such as an article, preprint, thesis or report, identified by its DOI or arXiv id when it has one.', 'research'),
  ('method', 'Entity', 'Method', 'A method, model, algorithm, intervention, instrument or experimental technique that a study proposes, applies or compares.', 'research'),
  ('measure', 'Entity', 'Measure', 'A metric, outcome or variable on which results are reported, such as accuracy, mortality or factual consistency.', 'research');

INSERT INTO kernel.edge_kinds (name, edge, label, description, defined_by) VALUES
  ('authored', 'responsible_for', 'authored', 'A person is an author of a paper.', 'research'),
  ('reports', 'responsible_for', 'reports', 'A paper reports a finding, hypothesis or open question, recorded as a Claim node.', 'research'),
  ('introduces', 'responsible_for', 'introduces', 'A paper first proposes a method, data set or measure.', 'research'),
  ('cites', 'depends_on', 'cites', 'A paper cites another paper.', 'research'),
  ('uses_data', 'depends_on', 'uses data', 'A paper or method uses a data set, corpus, benchmark or cohort.', 'research'),
  ('builds_on', 'depends_on', 'builds on', 'A method extends, adapts or is a variant of another method.', 'research');

INSERT INTO kernel.rules (id, category, namespace, params, label, description, defined_by) VALUES
  ('research.allowed_kinds', 'types', 'research',
   '{"kinds": ["paper", "method", "measure", "data", "concept", "component"]}',
   'Research kinds', 'The research namespace allows papers, methods, measures, data sets, concepts and software components.',
   'research'),
  ('research.authored_paper', 'domain_range', NULL,
   '{"edge": "responsible_for", "kind": "authored", "from": {"types": ["Agent"], "kinds": ["human"]}, "to": {"kinds": ["paper"]}}',
   'People author papers', 'authored goes from a person to a paper.', 'research'),
  ('research.reports_claim', 'domain_range', NULL,
   '{"edge": "responsible_for", "kind": "reports", "from": {"kinds": ["paper"]}, "to": {"types": ["Claim"]}}',
   'Papers report claims', 'reports goes from a paper to a Claim node: a finding, hypothesis or open question.', 'research'),
  ('research.introduces_artefact', 'domain_range', NULL,
   '{"edge": "responsible_for", "kind": "introduces", "from": {"kinds": ["paper"]}, "to": {"kinds": ["method", "data", "measure"]}}',
   'Papers introduce methods, data and measures', 'introduces goes from a paper to a method, data set or measure.', 'research'),
  ('research.cites_paper', 'domain_range', NULL,
   '{"edge": "depends_on", "kind": "cites", "from": {"kinds": ["paper"]}, "to": {"kinds": ["paper"]}}',
   'Papers cite papers', 'cites goes from a paper to a paper.', 'research'),
  ('research.uses_data', 'domain_range', NULL,
   '{"edge": "depends_on", "kind": "uses_data", "from": {"kinds": ["paper", "method"]}, "to": {"kinds": ["data"]}}',
   'Papers and methods use data', 'uses_data goes from a paper or method to a data set.', 'research'),
  ('research.builds_on_method', 'domain_range', NULL,
   '{"edge": "depends_on", "kind": "builds_on", "from": {"kinds": ["method"]}, "to": {"kinds": ["method"]}}',
   'Methods build on methods', 'builds_on goes from a method to the method it extends.', 'research'),
  ('research.paper_ids', 'identity', NULL,
   '{"node_type": "Entity", "kinds": ["paper"], "keys": ["doi", "arxiv"], "patterns": {"doi": "^10\\.[0-9]{4,9}/[^[:space:]]+$", "arxiv": "^([0-9]{4}\\.[0-9]{4,5}|[a-zA-Z.-]+/[0-9]{7})(v[0-9]+)?$"}}',
   'DOI and arXiv id identify a paper', 'Two papers with the same DOI or arXiv id are the same paper. Give the id without a version suffix when it applies to every version.', 'research'),
  ('research.orcid', 'identity', NULL,
   '{"node_type": "Agent", "kinds": ["human"], "keys": ["orcid"], "patterns": {"orcid": "^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9Xx]$"}}',
   'ORCID identifies a researcher', 'Two people with the same ORCID iD are the same person.', 'research');

RESET ROLE;
