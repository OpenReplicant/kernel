-- 85_ingest.sql
-- kernel.ingest_source: stores a source and its chunks; skips known content hashes.
-- Text and Markdown only until the parser container arrives.

SET ROLE kernel_owner;

-- Contract: splits the span [s, e) of p_content into pieces of at most 2 * p_max
-- characters, cutting at the last whitespace before p_max when there is one. Pure.
CREATE FUNCTION kernel.split_span(p_content text, s int, e int, p_max int)
RETURNS TABLE (piece_start int, piece_end int)
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  cut int;
  window_text text;
BEGIN
  WHILE e - s > 2 * p_max LOOP
    window_text := substring(p_content FROM s + 1 FOR p_max);
    cut := length(window_text) - coalesce(length(substring(window_text FROM '\s\S*$')), 0);
    IF cut <= 0 THEN
      cut := p_max;
    END IF;
    piece_start := s;
    piece_end := s + length(rtrim(substring(p_content FROM s + 1 FOR cut), E' \t\r\n'));
    RETURN NEXT;
    s := s + cut;
    WHILE s < e AND substring(p_content FROM s + 1 FOR 1) ~ '\s' LOOP
      s := s + 1;
    END LOOP;
  END LOOP;
  IF e > s THEN
    piece_start := s;
    piece_end := e;
    RETURN NEXT;
  END IF;
END
$$;

-- Contract: deterministic chunking with character spans [char_start, char_end) into
-- p_content (0-based, in characters). Paragraphs (separated by blank lines) are kept
-- whole and packed into chunks of about p_max characters; an oversize paragraph is
-- cut at whitespace. In Markdown every heading starts a new chunk and chunks carry
-- their heading path ('Invoices > Approval'); fenced code blocks are never split
-- at their inner headings. Pure.
CREATE FUNCTION kernel.chunk_text(p_content text, p_media_type text, p_max int DEFAULT 1500)
RETURNS TABLE (seq int, char_start int, char_end int, heading text, body text)
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  md boolean := p_media_type = 'text/markdown';
  line text;
  pos int := 0;
  cur_start int;
  cur_end int;
  cur_heading text;
  para_open boolean := false;
  in_fence boolean := false;
  headings text[] := '{}';
  level int;
  lead int;
  starts int[] := '{}';
  ends int[] := '{}';
  heads text[] := '{}';
  piece record;
  n int := 0;
BEGIN
  FOR line IN SELECT l FROM string_to_table(p_content, E'\n') WITH ORDINALITY AS t(l, i) ORDER BY i LOOP
    lead := length(line) - length(ltrim(line, E' \t\r'));
    IF md AND line ~ '^\s*(```|~~~)' THEN
      in_fence := NOT in_fence;
    END IF;
    IF md AND NOT in_fence AND line ~ '^#{1,6}\s+\S' THEN
      IF cur_start IS NOT NULL THEN
        starts := starts || cur_start; ends := ends || cur_end; heads := heads || cur_heading;
      END IF;
      level := length(substring(line FROM '^(#+)'));
      headings := headings[1:level - 1] || btrim(regexp_replace(line, '^#+\s+|\s+#*\s*$', '', 'g'));
      cur_heading := array_to_string(headings, ' > ');
      cur_start := pos + lead;
      cur_end := pos + length(rtrim(line, E' \t\r'));
      para_open := false;
    ELSIF line ~ '^\s*$' AND NOT in_fence THEN
      para_open := false;
    ELSE
      IF cur_start IS NULL THEN
        cur_start := pos + lead;
      ELSIF NOT para_open AND pos + length(rtrim(line, E' \t\r')) - cur_start > p_max THEN
        starts := starts || cur_start; ends := ends || cur_end; heads := heads || cur_heading;
        cur_start := pos + lead;
      END IF;
      para_open := true;
      IF length(rtrim(line, E' \t\r')) > 0 THEN
        cur_end := pos + length(rtrim(line, E' \t\r'));
      END IF;
    END IF;
    pos := pos + length(line) + 1;
  END LOOP;
  IF cur_start IS NOT NULL AND cur_end > cur_start THEN
    starts := starts || cur_start; ends := ends || cur_end; heads := heads || cur_heading;
  END IF;

  FOR i IN 1..coalesce(array_length(starts, 1), 0) LOOP
    FOR piece IN SELECT * FROM kernel.split_span(p_content, starts[i], ends[i], p_max) LOOP
      seq := n;
      char_start := piece.piece_start;
      char_end := piece.piece_end;
      heading := heads[i];
      body := substring(p_content FROM piece.piece_start + 1 FOR piece.piece_end - piece.piece_start);
      n := n + 1;
      RETURN NEXT;
    END LOOP;
  END LOOP;
END
$$;

-- Contract: kernel.ingest_source(source, agent_id) stores a source with stable chunk ids
-- and character spans, or returns the existing one when its content hash is already
-- known (per collection and uri for collections). Writes nothing else.
--
-- source: {"content", "media_type"?: "text/plain"|"text/markdown", "title"?, "uri"?,
--          "collection"?, "author"?: agent id, "metadata"?: {}, "trace_id"?, "span_id"?}
-- Returns {"source_id", "content_hash", "skipped", "chunks": [{"id", "seq", "char_start",
--          "char_end", "heading", "text"}]}.
CREATE FUNCTION kernel.ingest_source(p_source jsonb, p_agent_id text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, kernel, public, pg_temp
AS $$
DECLARE
  v_content text := p_source ->> 'content';
  v_media text := coalesce(p_source ->> 'media_type', 'text/plain');
  v_collection text := p_source ->> 'collection';
  v_uri text := p_source ->> 'uri';
  v_author text := p_source ->> 'author';
  v_hash text;
  v_id text;
  v_keys text;
BEGIN
  IF jsonb_typeof(p_source) IS DISTINCT FROM 'object' THEN
    PERFORM kernel.reject('payload', NULL, 'the source must be a JSON object');
  END IF;
  SELECT string_agg(k, ', ') INTO v_keys FROM jsonb_object_keys(p_source) k
  WHERE k NOT IN ('content', 'media_type', 'title', 'uri', 'collection', 'author', 'metadata', 'trace_id', 'span_id');
  IF v_keys IS NOT NULL THEN
    PERFORM kernel.reject('payload', NULL, format('unknown source keys: %s', v_keys));
  END IF;
  IF jsonb_typeof(p_source -> 'content') IS DISTINCT FROM 'string' OR btrim(v_content) = '' THEN
    PERFORM kernel.reject('payload', NULL, 'content is required', jsonb_build_object('field', 'content'));
  END IF;
  IF v_media NOT IN ('text/plain', 'text/markdown') THEN
    PERFORM kernel.reject('payload', NULL, 'media_type must be text/plain or text/markdown until the parser container arrives',
                          jsonb_build_object('field', 'media_type', 'nearest', '["text/plain", "text/markdown"]'::jsonb));
  END IF;
  IF p_source ? 'metadata' AND jsonb_typeof(p_source -> 'metadata') <> 'object' THEN
    PERFORM kernel.reject('payload', NULL, 'metadata must be an object', jsonb_build_object('field', 'metadata'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM kernel.nodes WHERE id = p_agent_id AND type = 'Agent') THEN
    PERFORM kernel.reject('agent', NULL, format('%s is not an agent', coalesce(p_agent_id, 'null')));
  END IF;
  IF v_author IS NOT NULL AND NOT EXISTS (SELECT 1 FROM kernel.nodes WHERE id = v_author AND type = 'Agent') THEN
    PERFORM kernel.reject('reference', NULL, format('author %s is not an agent id', v_author),
                          jsonb_build_object('field', 'author'));
  END IF;

  v_hash := encode(sha256(convert_to(v_content, 'UTF8')), 'hex');
  PERFORM pg_advisory_xact_lock(hashtextextended('kernel.source:' || coalesce(v_collection, '') || ':' || v_hash, 0));
  SELECT id INTO v_id FROM kernel.sources
  WHERE content_hash = v_hash
    AND ((v_collection IS NULL AND collection IS NULL)
         OR (collection = v_collection AND coalesce(uri, '') = coalesce(v_uri, '')));

  IF v_id IS NULL THEN
    v_id := kernel.new_id('src');
    INSERT INTO kernel.sources (id, content_hash, media_type, title, uri, collection, author_agent_id, content,
                                metadata, agent_id, recorded_at, trace_id, span_id)
    VALUES (v_id, v_hash, v_media, p_source ->> 'title', v_uri, v_collection, v_author, v_content,
            coalesce(p_source -> 'metadata', '{}'), p_agent_id, clock_timestamp(),
            p_source ->> 'trace_id', p_source ->> 'span_id');
    INSERT INTO kernel.chunks (id, source_id, seq, char_start, char_end, heading, text)
    SELECT 'chk_' || split_part(v_id, '_', 2) || '_' || lpad(ch.seq::text, 4, '0'), v_id, ch.seq,
           ch.char_start, ch.char_end, ch.heading, ch.body
    FROM kernel.chunk_text(v_content, v_media) ch;
    RETURN jsonb_build_object('source_id', v_id, 'content_hash', v_hash, 'skipped', false,
                              'chunks', kernel.source_chunks(v_id));
  END IF;
  RETURN jsonb_build_object('source_id', v_id, 'content_hash', v_hash, 'skipped', true,
                            'chunks', kernel.source_chunks(v_id));
END
$$;

-- Contract: the chunks of a source in order, as returned by ingest_source.
CREATE FUNCTION kernel.source_chunks(p_source_id text) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
           'id', id, 'seq', seq, 'char_start', char_start, 'char_end', char_end, 'heading', heading, 'text', text))
         ORDER BY seq), '[]')
  FROM kernel.chunks WHERE source_id = p_source_id
$$;
