-- 40_graph.sql
-- The graph: relational projection tables (nodes, edges, conflicts, touches,
-- redactions) and their mirror in the Apache AGE graph 'world' for Cypher reads.
-- Only projection functions write here, always inside a kernel write transaction.

SET ROLE kernel_owner;

CREATE TABLE kernel.nodes (
  id               text PRIMARY KEY,
  type             text NOT NULL REFERENCES kernel.node_types (name),
  kind             text NOT NULL,
  namespace        text NOT NULL REFERENCES kernel.namespaces (name),
  name             text NOT NULL,
  name_norm        text GENERATED ALWAYS AS (kernel.normalize_name(name)) STORED,
  aliases          text[] NOT NULL DEFAULT '{}',
  aliases_norm     text[] GENERATED ALWAYS AS (kernel.normalize_names(aliases)) STORED,
  identity         jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(identity) = 'object'),
  props            jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(props) = 'object'),
  embedding        vector,
  trust_level      text CHECK (trust_level IN ('low', 'medium', 'high')),
  status           text,
  status_options   text[] NOT NULL DEFAULT '{}',
  belief_status    text CHECK (belief_status IN ('accepted', 'contested', 'rejected', 'unknown')),
  belief_score     numeric,
  belief_for       numeric,
  belief_against   numeric,
  superseded_by    text[] NOT NULL DEFAULT '{}',
  redacted         text[] NOT NULL DEFAULT '{}',
  -- The data key the node's personal fields were opened from (ADR 0022): a human agent's
  -- own key, or for a Claim node its claim's text key. kernel.erase re-projects the nodes
  -- whose key it destroys.
  sealed_key       text,
  claim_id         text NOT NULL,
  created_offset   bigint NOT NULL,
  updated_offset   bigint NOT NULL,
  created_at       timestamptz NOT NULL,
  updated_at       timestamptz NOT NULL,
  CHECK ((type = 'Agent') = (trust_level IS NOT NULL)),
  CHECK ((type = 'Claim') = (belief_status IS NOT NULL))
);
COMMENT ON TABLE kernel.nodes IS
  'Projection: one row per node. status is the agreed lifecycle status or ''contested'' with status_options; belief_* is set for Claim nodes.';
CREATE INDEX nodes_lookup_idx ON kernel.nodes (type, kind, name_norm);
CREATE INDEX nodes_sealed_idx ON kernel.nodes (sealed_key) WHERE sealed_key IS NOT NULL;
-- GIN indexes here have fastupdate off: every create looks up names right after earlier
-- inserts, and a pending list would make each lookup scan it linearly until a vacuum.
CREATE INDEX nodes_trgm_idx ON kernel.nodes USING gin (name_norm gin_trgm_ops) WITH (fastupdate = off);
CREATE INDEX nodes_aliases_idx ON kernel.nodes USING gin (aliases_norm) WITH (fastupdate = off);
CREATE INDEX nodes_identity_idx ON kernel.nodes USING gin (identity jsonb_path_ops) WITH (fastupdate = off);

CREATE TABLE kernel.edges (
  id               text PRIMARY KEY,
  edge             text NOT NULL REFERENCES kernel.edge_types (name),
  kind             text REFERENCES kernel.edge_kinds (name),
  from_id          text NOT NULL REFERENCES kernel.nodes (id),
  to_id            text NOT NULL REFERENCES kernel.nodes (id),
  props            jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(props) = 'object'),
  valid_from       timestamptz,
  valid_to         timestamptz,
  window_agreed    boolean NOT NULL DEFAULT true,
  belief_status    text NOT NULL DEFAULT 'unknown' CHECK (belief_status IN ('accepted', 'contested', 'rejected', 'unknown')),
  belief_score     numeric,
  belief_for       numeric NOT NULL DEFAULT 0,
  belief_against   numeric NOT NULL DEFAULT 0,
  sources_for      int NOT NULL DEFAULT 0,
  sources_against  int NOT NULL DEFAULT 0,
  origins_for      int NOT NULL DEFAULT 0,
  origins_against  int NOT NULL DEFAULT 0,
  contested_with   text[] NOT NULL DEFAULT '{}',
  claim_id         text NOT NULL,
  created_offset   bigint NOT NULL,
  updated_offset   bigint NOT NULL,
  created_at       timestamptz NOT NULL,
  updated_at       timestamptz NOT NULL
);
COMMENT ON TABLE kernel.edges IS
  'Projection: one row per edge. The window is the union of what counted sources assert; belief_* follows kernel.belief_v2; contested_with lists edges in an active single-valued conflict.';
CREATE INDEX edges_from_idx ON kernel.edges (from_id, edge);
CREATE INDEX edges_to_idx ON kernel.edges (to_id, edge);

CREATE TABLE kernel.conflicts (
  edge_a     text NOT NULL REFERENCES kernel.edges (id),
  edge_b     text NOT NULL REFERENCES kernel.edges (id),
  rule       text NOT NULL,
  log_offset bigint NOT NULL,
  recorded_at timestamptz NOT NULL,
  PRIMARY KEY (edge_a, edge_b, rule),
  CHECK (edge_a < edge_b)
);
COMMENT ON TABLE kernel.conflicts IS
  'Projection: single-valued conflicts between edges asserted by different sources, as recorded in the log entry that found them. Active while both edges stand and their windows overlap.';
CREATE INDEX conflicts_b_idx ON kernel.conflicts (edge_b);

CREATE TABLE kernel.node_touches (
  node_id    text NOT NULL,
  log_offset bigint NOT NULL,
  agent_id   text NOT NULL,
  PRIMARY KEY (node_id, log_offset)
);
COMMENT ON TABLE kernel.node_touches IS
  'Projection: every log entry that changed a node or an edge at it. Used by the stale-read check.';

CREATE TABLE kernel.claim_redactions (
  claim_id   text PRIMARY KEY,
  log_offset bigint NOT NULL
);
COMMENT ON TABLE kernel.claim_redactions IS
  'Projection: claims whose text is redacted in every read path.';

-- Apache AGE mirror ----------------------------------------------------------------
-- Labels are created once: the four node types and the kernel edges. The graph is
-- created as superuser because AGE writes its catalog; the owner gets DML rights.

RESET ROLE;
SET search_path = ag_catalog, "$user", public;

SELECT ag_catalog.create_graph('world');
SELECT ag_catalog.create_vlabel('world', name::cstring) FROM kernel.node_types ORDER BY name;
SELECT ag_catalog.create_elabel('world', name::cstring) FROM kernel.edge_types ORDER BY name;

DO $$
DECLARE
  label text;
BEGIN
  FOR label IN SELECT name FROM kernel.node_types UNION ALL SELECT name FROM kernel.edge_types LOOP
    -- GIN serves Cypher property matches; the btree on id serves the mirror's own lookups.
    EXECUTE format('CREATE INDEX ON world.%I USING gin (properties)', label);
    EXECUTE format('CREATE INDEX ON world.%I (ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, ''"id"''::ag_catalog.agtype]))', label);
  END LOOP;
END
$$;

GRANT USAGE ON SCHEMA world TO kernel_owner, kernel_reader;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA world TO kernel_owner;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA world TO kernel_owner;
GRANT SELECT ON ALL TABLES IN SCHEMA world TO kernel_reader;

RESET search_path;
SET ROLE kernel_owner;

-- Contract: the properties a node carries in the AGE graph: identifiers, kind,
-- names, status, belief (Claim nodes), event times and props. Timestamps use
-- kernel.iso. Null fields are omitted. Pure.
CREATE FUNCTION kernel.node_graph_props(n kernel.nodes) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_strip_nulls(jsonb_build_object(
    'id', n.id, 'type', n.type, 'kind', n.kind, 'namespace', n.namespace, 'name', n.name,
    'aliases', to_jsonb(n.aliases), 'status', n.status,
    'status_options', CASE WHEN n.status = 'contested' THEN to_jsonb(n.status_options) END,
    'trust_level', n.trust_level,
    'belief_status', n.belief_status, 'belief_score', n.belief_score,
    'superseded_by', CASE WHEN n.superseded_by <> '{}' THEN to_jsonb(n.superseded_by) END,
    'props', CASE WHEN n.props <> '{}' THEN n.props END,
    'created_offset', n.created_offset, 'updated_offset', n.updated_offset,
    'recorded_at', kernel.iso(n.created_at)))
$$;

-- Contract: the properties an edge carries in the AGE graph: id, kernel edge, kind,
-- validity window, belief and offsets. Null fields are omitted. Pure.
CREATE FUNCTION kernel.edge_graph_props(e kernel.edges) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_strip_nulls(jsonb_build_object(
    'id', e.id, 'edge', e.edge, 'kind', e.kind, 'from', e.from_id, 'to', e.to_id,
    'valid_from', kernel.iso(e.valid_from), 'valid_to', kernel.iso(e.valid_to),
    'window_agreed', e.window_agreed,
    'belief_status', e.belief_status, 'belief_score', e.belief_score,
    'sources_for', e.sources_for, 'sources_against', e.sources_against,
    'origins_for', e.origins_for, 'origins_against', e.origins_against,
    'contested_with', CASE WHEN e.contested_with <> '{}' THEN to_jsonb(e.contested_with) END,
    'props', CASE WHEN e.props <> '{}' THEN e.props END,
    'created_offset', e.created_offset, 'updated_offset', e.updated_offset,
    'recorded_at', kernel.iso(e.created_at)))
$$;

-- Contract: trigger keeping the AGE vertex of a node equal to node_graph_props.
-- Deterministic: no clock, no randomness, no network.
CREATE FUNCTION kernel.mirror_node() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  props ag_catalog.agtype := kernel.node_graph_props(NEW)::text::ag_catalog.agtype;
BEGIN
  IF TG_OP = 'INSERT' THEN
    EXECUTE format('INSERT INTO world.%I (properties) VALUES ($1)', NEW.type) USING props;
  ELSE
    EXECUTE format('UPDATE world.%I SET properties = $1 WHERE ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, ''"id"''::ag_catalog.agtype]) OPERATOR(ag_catalog.=) $2', NEW.type)
      USING props, to_jsonb(NEW.id)::text::ag_catalog.agtype;
  END IF;
  RETURN NULL;
END
$$;

-- Contract: trigger keeping the AGE edge of an edge equal to edge_graph_props,
-- connecting the AGE vertices of its endpoints. Deterministic.
CREATE FUNCTION kernel.mirror_edge() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  props ag_catalog.agtype := kernel.edge_graph_props(NEW)::text::ag_catalog.agtype;
  from_type text;
  to_type text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    SELECT type INTO from_type FROM kernel.nodes WHERE id = NEW.from_id;
    SELECT type INTO to_type FROM kernel.nodes WHERE id = NEW.to_id;
    EXECUTE format(
      'INSERT INTO world.%I (start_id, end_id, properties)
       SELECT s.id, t.id, $1 FROM world.%I s, world.%I t
       WHERE ag_catalog.agtype_access_operator(VARIADIC ARRAY[s.properties, ''"id"''::ag_catalog.agtype]) OPERATOR(ag_catalog.=) $2
         AND ag_catalog.agtype_access_operator(VARIADIC ARRAY[t.properties, ''"id"''::ag_catalog.agtype]) OPERATOR(ag_catalog.=) $3',
      NEW.edge, from_type, to_type)
    USING props, to_jsonb(NEW.from_id)::text::ag_catalog.agtype, to_jsonb(NEW.to_id)::text::ag_catalog.agtype;
  ELSE
    EXECUTE format('UPDATE world.%I SET properties = $1 WHERE ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, ''"id"''::ag_catalog.agtype]) OPERATOR(ag_catalog.=) $2', NEW.edge)
      USING props, to_jsonb(NEW.id)::text::ag_catalog.agtype;
  END IF;
  RETURN NULL;
END
$$;

CREATE TRIGGER nodes_mirror AFTER INSERT OR UPDATE ON kernel.nodes
  FOR EACH ROW EXECUTE FUNCTION kernel.mirror_node();
CREATE TRIGGER edges_mirror AFTER INSERT OR UPDATE ON kernel.edges
  FOR EACH ROW EXECUTE FUNCTION kernel.mirror_edge();
