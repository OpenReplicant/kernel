-- 10_sources.sql
-- Sources and their chunks. Append-only; written only by kernel.ingest_source.

SET ROLE kernel_owner;

CREATE TABLE kernel.sources (
  id              text PRIMARY KEY,
  content_hash    text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  media_type      text NOT NULL CHECK (media_type IN ('text/plain', 'text/markdown')),
  title           text,
  uri             text,
  -- Sources in one collection (for example the turns of one conversation) count as
  -- one source for belief. NULL means the source stands alone.
  collection      text,
  author_agent_id text,
  content         text NOT NULL,
  metadata        jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object'),
  agent_id        text NOT NULL,
  recorded_at     timestamptz NOT NULL,
  trace_id        text,
  span_id         text
);
COMMENT ON TABLE kernel.sources IS
  'Ingested sources. Known content hashes are skipped, per collection. Append-only.';

-- A document is skipped when its content hash is known; a turn in a collection is
-- skipped only when the same content arrives again at the same uri in that collection.
CREATE UNIQUE INDEX sources_hash_uniq ON kernel.sources (content_hash) WHERE collection IS NULL;
CREATE UNIQUE INDEX sources_collection_hash_uniq
  ON kernel.sources (collection, content_hash, coalesce(uri, '')) WHERE collection IS NOT NULL;

CREATE TABLE kernel.chunks (
  id         text PRIMARY KEY,
  source_id  text NOT NULL REFERENCES kernel.sources (id),
  seq        int NOT NULL CHECK (seq >= 0),
  page       int,
  char_start int NOT NULL CHECK (char_start >= 0),
  char_end   int NOT NULL CHECK (char_end > char_start),
  heading    text,
  text       text NOT NULL,
  UNIQUE (source_id, seq)
);
COMMENT ON TABLE kernel.chunks IS
  'Chunks of a source with character spans [char_start, char_end) into the source content. Append-only.';

CREATE TRIGGER sources_append_only BEFORE UPDATE OR DELETE ON kernel.sources
  FOR EACH ROW EXECUTE FUNCTION kernel.forbid_change();
CREATE TRIGGER sources_no_truncate BEFORE TRUNCATE ON kernel.sources
  FOR EACH STATEMENT EXECUTE FUNCTION kernel.forbid_change();
CREATE TRIGGER chunks_append_only BEFORE UPDATE OR DELETE ON kernel.chunks
  FOR EACH ROW EXECUTE FUNCTION kernel.forbid_change();
CREATE TRIGGER chunks_no_truncate BEFORE TRUNCATE ON kernel.chunks
  FOR EACH STATEMENT EXECUTE FUNCTION kernel.forbid_change();
