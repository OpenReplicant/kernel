-- The kernel core (docs/CORE.md). Idempotent. Installed by kernel.db.install_core(), which
-- substitutes __GRAPH__ and runs this with kernel.allow_ddl = on.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS kernel;

-- Limits the loop guard and the bridge read. A table, not settings: a writer can't raise them.
CREATE TABLE IF NOT EXISTS kernel.config (key text PRIMARY KEY, value jsonb NOT NULL);
INSERT INTO kernel.config VALUES ('max_depth', '8'), ('max_fanout', '1000'), ('max_attempts', '5')
  ON CONFLICT (key) DO NOTHING;

-- Graph schemas whose labels only the module loader may create.
CREATE TABLE IF NOT EXISTS kernel.managed_graph (name text PRIMARY KEY);

CREATE TABLE IF NOT EXISTS kernel.outbox (
  seq         bigserial PRIMARY KEY,
  graph       text NOT NULL,
  label       text NOT NULL,
  op          text NOT NULL,
  element_id  text NOT NULL,
  old_props   jsonb,
  new_props   jsonb,
  causation   text,
  depth       int NOT NULL,
  txid        bigint NOT NULL DEFAULT txid_current(),
  created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'stopped', 'dead')),
  attempts    int NOT NULL DEFAULT 0,
  delivered   jsonb NOT NULL DEFAULT '[]',   -- chains that ran successfully for this row
  results     jsonb NOT NULL DEFAULT '{}',   -- chain -> its output
  last_error  text,
  next_try_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  done_at     timestamptz
);
CREATE INDEX IF NOT EXISTS outbox_pending ON kernel.outbox (next_try_at, seq) WHERE status = 'pending';

-- Fixed-width ISO-8601 UTC, so times order correctly as text (and as jsonb strings).
CREATE OR REPLACE FUNCTION kernel.now_text() RETURNS text LANGUAGE sql VOLATILE AS $$
  SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
$$;

CREATE OR REPLACE FUNCTION kernel.setting_int(name text, dflt int) RETURNS int LANGUAGE sql STABLE AS $$
  SELECT coalesce(nullif(current_setting(name, true), '')::int, dflt)
$$;

-- A missing limit is an error, not NULL: a NULL limit would switch the guard off.
CREATE OR REPLACE FUNCTION kernel.limit_of(k text) RETURNS int LANGUAGE plpgsql STABLE AS $$
DECLARE v int;
BEGIN
  SELECT (value #>> '{}')::int INTO v FROM kernel.config WHERE key = k;
  IF v IS NULL THEN
    RAISE EXCEPTION 'kernel: limit % missing from kernel.config', k;
  END IF;
  RETURN v;
END $$;

-- BEFORE trigger on every kernel-managed label table. TG_ARGV[0] is the table's mode:
--   claim  claim vertices: provenance required, immutable but for retiring once
--   fact   fact edges: same rules as claims
--   record source, agent, module vertices: immutable but for retiring once
--   thing  things: stamped; identity (key) fixed, other properties may change
--   link   structural edges (about, object, cites, made_by, supersedes): immutable
-- Nothing is ever deleted.
CREATE OR REPLACE FUNCTION kernel.guard_row() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  mode text := TG_ARGV[0];
  p jsonb;
  conf numeric;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'kernel: % % is never deleted (supersede or retract instead)', TG_TABLE_NAME, OLD.id;
  END IF;

  IF TG_OP = 'INSERT' THEN
    p := NEW.properties;
    IF p ? 'retired_at' OR p ? 'status' THEN
      RAISE EXCEPTION 'kernel: % is born held; status and retired_at are set by supersede/retract', TG_TABLE_NAME;
    END IF;
    IF mode IN ('claim', 'fact') THEN
      -- (coalesce: a missing key is NULL, and IF NULL would let it through)
      IF NOT coalesce(p->>'method', '') = ANY (ARRAY['observed', 'stated', 'inferred', 'computed', 'defaulted']) THEN
        RAISE EXCEPTION 'kernel: % needs method observed|stated|inferred|computed|defaulted, got %', TG_TABLE_NAME, p->'method';
      END IF;
      IF jsonb_typeof(p->'confidence') IS DISTINCT FROM 'number' THEN
        RAISE EXCEPTION 'kernel: % needs a numeric confidence', TG_TABLE_NAME;
      END IF;
      conf := (p->>'confidence')::numeric;
      IF conf < 0 OR conf > 1 THEN
        RAISE EXCEPTION 'kernel: % confidence % outside 0..1', TG_TABLE_NAME, conf;
      END IF;
      IF coalesce(p->>'source', '') = '' OR coalesce(p->>'agent', '') = '' THEN
        RAISE EXCEPTION 'kernel: % needs a source and an agent', TG_TABLE_NAME;
      END IF;
      IF p->>'method' IN ('observed', 'stated') AND p->'span' IS NULL THEN
        RAISE EXCEPTION 'kernel: an % % needs a span in its source', p->>'method', TG_TABLE_NAME;
      END IF;
      -- (no null values: Cypher SET strips null keys, which would read as an edit)
      p := p || jsonb_strip_nulls(jsonb_build_object(
        'causation', nullif(current_setting('kernel.causation', true), ''),
        'depth', kernel.setting_int('kernel.depth', 0)));
    END IF;
    NEW.properties := jsonb_strip_nulls(p) || jsonb_build_object('recorded_at', kernel.now_text());
    RETURN NEW;
  END IF;

  -- UPDATE
  IF mode = 'link' THEN
    RAISE EXCEPTION 'kernel: % % is immutable', TG_TABLE_NAME, OLD.id;
  END IF;
  IF mode = 'thing' THEN
    IF NEW.properties->'key' IS DISTINCT FROM OLD.properties->'key'
       OR NEW.properties->'recorded_at' IS DISTINCT FROM OLD.properties->'recorded_at' THEN
      RAISE EXCEPTION 'kernel: a thing''s key and recorded_at are fixed (%)', OLD.id;
    END IF;
    RETURN NEW;
  END IF;
  -- claim, fact, record: the only change is retiring, once
  IF OLD.properties ? 'retired_at' THEN
    RAISE EXCEPTION 'kernel: % % is retired and immutable', TG_TABLE_NAME, OLD.id;
  END IF;
  IF jsonb_strip_nulls(NEW.properties - 'status') IS DISTINCT FROM jsonb_strip_nulls(OLD.properties)
     OR NOT coalesce(NEW.properties->>'status', '') = ANY (ARRAY['superseded', 'retracted']) THEN
    RAISE EXCEPTION 'kernel: % % is immutable; the only change is status superseded|retracted', TG_TABLE_NAME, OLD.id;
  END IF;
  NEW.properties := NEW.properties || jsonb_build_object('retired_at', kernel.now_text());
  RETURN NEW;
END $$;

-- AFTER trigger: the loop guard, then the outbox row and NOTIFY, in the writer's transaction.
CREATE OR REPLACE FUNCTION kernel.capture() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  depth int := kernel.setting_int('kernel.depth', 0);
  writes int := kernel.setting_int('kernel.writes', 0) + 1;
  s bigint;
BEGIN
  IF depth > kernel.limit_of('max_depth') THEN
    RAISE EXCEPTION 'kernel loop guard: depth % exceeds max_depth % (causation %)',
      depth, kernel.limit_of('max_depth'), current_setting('kernel.causation', true);
  END IF;
  IF writes > kernel.limit_of('max_fanout') THEN
    RAISE EXCEPTION 'kernel loop guard: % writes in one transaction exceed max_fanout % (causation %)',
      writes, kernel.limit_of('max_fanout'), current_setting('kernel.causation', true);
  END IF;
  PERFORM set_config('kernel.writes', writes::text, true);
  INSERT INTO kernel.outbox (graph, label, op, element_id, old_props, new_props, causation, depth)
  VALUES (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP,
          (CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END)::text,
          CASE WHEN TG_OP <> 'INSERT' THEN OLD.properties END,
          CASE WHEN TG_OP <> 'DELETE' THEN NEW.properties END,
          nullif(current_setting('kernel.causation', true), ''), depth)
  RETURNING seq INTO s;
  PERFORM pg_notify('outbox', s::text);
  RETURN NULL;
END $$;

-- Put the kernel's triggers on a label table.
CREATE OR REPLACE FUNCTION kernel.manage(tbl regclass, mode text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF mode NOT IN ('claim', 'fact', 'record', 'thing', 'link') THEN
    RAISE EXCEPTION 'kernel.manage: unknown mode %', mode;
  END IF;
  EXECUTE format('DROP TRIGGER IF EXISTS k_guard ON %s', tbl);
  EXECUTE format('CREATE TRIGGER k_guard BEFORE INSERT OR UPDATE OR DELETE ON %s
                  FOR EACH ROW EXECUTE FUNCTION kernel.guard_row(%L)', tbl, mode);
  EXECUTE format('DROP TRIGGER IF EXISTS k_outbox ON %s', tbl);
  IF mode <> 'link' THEN
    EXECUTE format('CREATE TRIGGER k_outbox AFTER INSERT OR UPDATE OR DELETE ON %s
                    FOR EACH ROW EXECUTE FUNCTION kernel.capture()', tbl);
  END IF;
END $$;

-- Writes to the unlabelled base tables are refused: every element has a declared label.
CREATE OR REPLACE FUNCTION kernel.refuse_unlabelled() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'kernel: unlabelled elements are not allowed (%)', TG_TABLE_NAME;
END $$;

-- Cypher creates a label it hasn't seen (an internal CREATE VLABEL/ELABEL). In a managed
-- graph only the module loader, which sets kernel.allow_ddl in its transaction, may do that.
CREATE OR REPLACE FUNCTION kernel.refuse_undeclared() RETURNS event_trigger LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
  IF coalesce(current_setting('kernel.allow_ddl', true), '') = 'on' THEN
    RETURN;
  END IF;
  FOR r IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
    IF r.schema_name IN (SELECT name FROM kernel.managed_graph) THEN
      RAISE EXCEPTION 'kernel: undeclared label or schema change in graph % (%: %); labels come from module ontologies',
        r.schema_name, r.command_tag, r.object_identity;
    END IF;
  END LOOP;
END $$;
DROP EVENT TRIGGER IF EXISTS kernel_refuse_undeclared;
CREATE EVENT TRIGGER kernel_refuse_undeclared ON ddl_command_end
  EXECUTE FUNCTION kernel.refuse_undeclared();

-- The graph and its core labels.
CREATE GRAPH IF NOT EXISTS __GRAPH__;
INSERT INTO kernel.managed_graph VALUES ('__GRAPH__') ON CONFLICT DO NOTHING;
SET graph_path = __GRAPH__;
CREATE VLABEL IF NOT EXISTS thing;
CREATE VLABEL IF NOT EXISTS claim;
CREATE VLABEL IF NOT EXISTS source;
CREATE VLABEL IF NOT EXISTS agent;
CREATE VLABEL IF NOT EXISTS module;
CREATE ELABEL IF NOT EXISTS about;
CREATE ELABEL IF NOT EXISTS object;
CREATE ELABEL IF NOT EXISTS cites;
CREATE ELABEL IF NOT EXISTS made_by;
CREATE ELABEL IF NOT EXISTS supersedes;
CREATE PROPERTY INDEX IF NOT EXISTS claim_predicate ON claim (predicate);
CREATE PROPERTY INDEX IF NOT EXISTS thing_key ON thing (key);
CREATE PROPERTY INDEX IF NOT EXISTS source_sha256 ON source (sha256);
CREATE PROPERTY INDEX IF NOT EXISTS agent_name ON agent (name);
CREATE PROPERTY INDEX IF NOT EXISTS module_name ON module (name);

SELECT kernel.manage('__GRAPH__.thing', 'thing');
SELECT kernel.manage('__GRAPH__.claim', 'claim');
SELECT kernel.manage('__GRAPH__.source', 'record');
SELECT kernel.manage('__GRAPH__.agent', 'record');
SELECT kernel.manage('__GRAPH__.module', 'record');
SELECT kernel.manage('__GRAPH__.' || l, 'link') FROM unnest(ARRAY['about', 'object', 'cites', 'made_by', 'supersedes']) l;

DROP TRIGGER IF EXISTS k_unlabelled ON __GRAPH__.ag_vertex;
CREATE TRIGGER k_unlabelled BEFORE INSERT ON __GRAPH__.ag_vertex FOR EACH ROW EXECUTE FUNCTION kernel.refuse_unlabelled();
DROP TRIGGER IF EXISTS k_unlabelled ON __GRAPH__.ag_edge;
CREATE TRIGGER k_unlabelled BEFORE INSERT ON __GRAPH__.ag_edge FOR EACH ROW EXECUTE FUNCTION kernel.refuse_unlabelled();
