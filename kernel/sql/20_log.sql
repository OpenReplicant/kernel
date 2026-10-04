-- 20_log.sql
-- The log (source of truth), the claim and assertion records projected from it, and
-- cite records. All append-only for every role, enforced by triggers and grants.

SET ROLE kernel_owner;

-- Contract: true when every element of ops is a resolved operation in kernel
-- vocabulary: an object whose "op" is one of the seven operations and whose keys
-- are all known for that operation. Used as a CHECK on kernel.log so the log can
-- never hold a query, SQL or an instruction to call a model. Pure.
CREATE FUNCTION kernel.is_kernel_vocabulary(ops jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT jsonb_typeof(ops) = 'array' AND NOT EXISTS (
    SELECT 1
    FROM jsonb_array_elements(ops) AS o(op)
    WHERE jsonb_typeof(o.op) <> 'object'
       OR NOT (o.op ->> 'op' = ANY (ARRAY['create', 'assert', 'link', 'unlink', 'promote', 'transition', 'redact']))
       OR EXISTS (
         SELECT 1 FROM jsonb_object_keys(o.op) AS k(key)
         WHERE NOT (k.key = ANY (CASE o.op ->> 'op'
           WHEN 'create' THEN ARRAY['op', 'id', 'ref', 'type', 'kind', 'namespace', 'name', 'aliases', 'identity',
                                    'props', 'status', 'trust_level', 'start', 'end', 'embedding', 'self',
                                    'distinct_from']
           WHEN 'assert' THEN ARRAY['op', 'ref', 'target', 'edge_id', 'new_edge', 'edge', 'kind', 'from', 'to',
                                    'props', 'valid_from', 'valid_to', 'polarity', 'claim_node']
           WHEN 'link' THEN ARRAY['op', 'edge_id', 'new_edge', 'from', 'to', 'polarity']
           WHEN 'unlink' THEN ARRAY['op', 'edge_id', 'new_edge', 'from', 'to', 'polarity']
           WHEN 'promote' THEN ARRAY['op', 'ref', 'node_id', 'about_edges', 'props']
           WHEN 'transition' THEN ARRAY['op', 'node', 'status']
           WHEN 'redact' THEN ARRAY['op', 'node', 'claim', 'fields']
         END))
       )
  )
$$;

CREATE TABLE kernel.log (
  log_offset     bigint PRIMARY KEY CHECK (log_offset > 0),
  entry_id       uuid NOT NULL UNIQUE,
  kind           text NOT NULL DEFAULT 'write' CHECK (kind = 'write'),
  agent_id       text NOT NULL,
  agent_trust    text NOT NULL CHECK (agent_trust IN ('low', 'medium', 'high')),
  claim          jsonb NOT NULL CHECK (jsonb_typeof(claim) = 'object'),
  ops            jsonb NOT NULL CHECK (kernel.is_kernel_vocabulary(ops)),
  conflicts      jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(conflicts) = 'array'),
  read_at_offset bigint NOT NULL CHECK (read_at_offset >= 0 AND read_at_offset < log_offset),
  trace_id       text,
  span_id        text,
  recorded_at    timestamptz NOT NULL,
  belief_version int NOT NULL
);
COMMENT ON TABLE kernel.log IS
  'The append-only log: one entry per accepted write, gapless offsets, resolved operations in kernel vocabulary. The graph is its projection.';
CREATE INDEX log_agent_idx ON kernel.log (agent_id, log_offset);
CREATE INDEX log_recorded_idx ON kernel.log (recorded_at);

CREATE TABLE kernel.claims (
  id          text PRIMARY KEY,
  log_offset  bigint NOT NULL UNIQUE REFERENCES kernel.log (log_offset) DEFERRABLE INITIALLY DEFERRED,
  text        text NOT NULL CHECK (text <> ''),
  chunk_id    text REFERENCES kernel.chunks (id),
  source_id   text REFERENCES kernel.sources (id),
  -- Belief groups assertions by this key: the source's collection, else the source,
  -- else the writing agent for claims without a source.
  source_key  text NOT NULL,
  agent_id    text NOT NULL,
  basis       text NOT NULL CHECK (basis IN ('observed', 'reported', 'inferred')),
  modality    text NOT NULL CHECK (modality IN ('descriptive', 'predictive', 'normative', 'proposed', 'hypothetical')),
  polarity    smallint NOT NULL CHECK (polarity IN (1, -1)),
  confidence  text NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
  -- The trust level that weighs this claim's assertions: the writer's, or for a reported
  -- claim citing a source with an author, the lower of the writer's and the author's.
  trust       text NOT NULL CHECK (trust IN ('low', 'medium', 'high')),
  resolution  text NOT NULL CHECK (resolution IN ('resolved', 'unresolved')),
  unresolved  jsonb,
  recorded_at timestamptz NOT NULL,
  trace_id    text,
  span_id     text,
  -- The words of the source the claim rests on: 0-based character offsets into the
  -- source's content, found by kernel.find_quote when the claim was written.
  quote_start int CHECK (quote_start >= 0),
  quote_end   int CHECK (quote_end > quote_start),
  -- The extraction run (an Event of kind extraction) the claim was written in.
  run_id      text,
  CHECK ((quote_start IS NULL) = (quote_end IS NULL))
);
COMMENT ON TABLE kernel.claims IS
  'One claim per log entry. Unresolved claims keep text and provenance with no operations. Append-only.';
CREATE INDEX claims_source_idx ON kernel.claims (source_id);
CREATE INDEX claims_run_idx ON kernel.claims (run_id) WHERE run_id IS NOT NULL;
CREATE INDEX claims_unresolved_idx ON kernel.claims (log_offset) WHERE resolution = 'unresolved';

CREATE TABLE kernel.assertions (
  id          text PRIMARY KEY,
  log_offset  bigint NOT NULL REFERENCES kernel.log (log_offset) DEFERRABLE INITIALLY DEFERRED,
  op_index    int NOT NULL,
  claim_id    text NOT NULL REFERENCES kernel.claims (id) DEFERRABLE INITIALLY DEFERRED,
  agent_id    text NOT NULL,
  source_key  text NOT NULL,
  target_type text NOT NULL CHECK (target_type IN ('edge', 'claim', 'status')),
  target_id   text NOT NULL,
  polarity    smallint NOT NULL CHECK (polarity IN (1, -1)),
  valid_from  timestamptz,
  valid_to    timestamptz,
  value       text,
  basis       text NOT NULL,
  modality    text NOT NULL,
  confidence  text NOT NULL,
  weight      numeric NOT NULL CHECK (weight > 0),
  recorded_at timestamptz NOT NULL,
  CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_from < valid_to),
  CHECK ((target_type = 'status') = (value IS NOT NULL))
);
COMMENT ON TABLE kernel.assertions IS
  'Source, agent, time, confidence, polarity, modality and basis backing an edge, a claim node or a node status. Append-only.';
CREATE INDEX assertions_target_idx ON kernel.assertions (target_type, target_id, source_key, log_offset DESC, op_index DESC);
CREATE INDEX assertions_claim_idx ON kernel.assertions (claim_id);

CREATE TABLE kernel.cites (
  id             text PRIMARY KEY,
  answer_id      text NOT NULL,
  sentence_index int NOT NULL CHECK (sentence_index >= 0),
  sentence       text NOT NULL,
  assertion_ids  text[] NOT NULL,
  edge_ids       text[] NOT NULL DEFAULT '{}',
  claim_ids      text[] NOT NULL DEFAULT '{}',
  at_offset      bigint NOT NULL,
  agent_id       text NOT NULL,
  recorded_at    timestamptz NOT NULL,
  trace_id       text,
  span_id        text,
  UNIQUE (answer_id, sentence_index)
);
COMMENT ON TABLE kernel.cites IS
  'Which assertions each answer sentence relied on, as known at at_offset. Append-only.';

DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['log', 'claims', 'assertions', 'cites'] LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON kernel.%I FOR EACH ROW EXECUTE FUNCTION kernel.forbid_change()',
                   t || '_append_only', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON kernel.%I FOR EACH STATEMENT EXECUTE FUNCTION kernel.forbid_change()',
                   t || '_no_truncate', t);
  END LOOP;
END
$$;
