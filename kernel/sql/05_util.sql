-- 05_util.sql
-- Identifiers, name normalisation, timestamps, append-only guard and rejections.

SET ROLE kernel_owner;

-- Contract: encodes a UUID's 128 bits as a 26-character Crockford base32 string
-- (ULID text form). For a UUIDv7 the result sorts by creation time. Pure.
CREATE FUNCTION kernel.crockford32(u uuid) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
DECLARE
  alphabet constant text := '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
  bits bit varying := B'00' || ('x' || encode(uuid_send(u), 'hex'))::bit(128);
  result text := '';
BEGIN
  FOR i IN 0..25 LOOP
    result := result || substr(alphabet, substring(bits FROM i * 5 + 1 FOR 5)::bit(5)::int + 1, 1);
  END LOOP;
  RETURN result;
END
$$;

-- Contract: returns a new identifier '<prefix>_<ULID>' from a fresh UUIDv7.
-- Volatile. Called only by the write functions when an entry is logged; the
-- projection never calls it, it reads identifiers from the log entry.
CREATE FUNCTION kernel.new_id(prefix text) RETURNS text
LANGUAGE sql VOLATILE AS $$
  SELECT prefix || '_' || kernel.crockford32(uuidv7())
$$;

-- Contract: canonical form of a name for exact matching: Latin letters folded to ASCII
-- (José -> jose, Straße -> strasse) by a fixed table, lower case, every run of
-- non-alphanumeric characters replaced by one space, trimmed. Pure and locale-independent
-- for these letters, so stored normalized names replay identically.
CREATE FUNCTION kernel.normalize_name(name text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT btrim(regexp_replace(
    translate(
      replace(replace(replace(replace(replace(replace(replace(replace(replace(lower(name), 'ß', 'ss'), 'æ', 'ae'), 'œ', 'oe'), 'þ', 'th'), 'ĳ', 'ij'), 'Æ', 'ae'), 'Œ', 'oe'), 'Þ', 'th'), 'Ĳ', 'ij'),
      'ÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝàáâãäåçèéêëìíîïðñòóôõöøùúûüýÿĀāĂăĄąĆćĈĉĊċČčĎďĐđĒēĔĕĖėĘęĚěĜĝĞğĠġĢģĤĥĦħĨĩĪīĬĭĮįİıĴĵĶķĸĹĺĻļĽľĿŀŁłŃńŅņŇňŉŊŋŌōŎŏŐőŔŕŖŗŘřŚśŜŝŞşŠšŢţŤťŦŧŨũŪūŬŭŮůŰűŲųŴŵŶŷŸŹźŻżŽžſ',
      'aaaaaaceeeeiiiidnoooooouuuuyaaaaaaceeeeiiiidnoooooouuuuyyaaaaaaccccccccddddeeeeeeeeeegggggggghhhhiiiiiiiiiijjkkkllllllllllnnnnnnnnnoooooorrrrrrssssssssttttttuuuuuuuuuuuuwwyyyzzzzzzs'),
    '[^[:alnum:]]+', ' ', 'g'))
$$;

-- Contract: the name of a Claim node made from a claim's text: the whole text up to 500
-- characters (one sentence, per the skills), else cut at the last word boundary before
-- 500 with an ellipsis, so a name never ends mid-word and never hides a negation. The cap
-- keeps names within index row limits. Pure.
CREATE FUNCTION kernel.claim_node_name(p_text text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT CASE WHEN length(p_text) <= 500 THEN p_text
              ELSE regexp_replace(left(p_text, 500), '[[:space:]]+[^[:space:]]*$', '') || '…' END
$$;

-- Contract: normalize_name applied to every element, order kept. Pure.
CREATE FUNCTION kernel.normalize_names(names text[]) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT coalesce(array_agg(kernel.normalize_name(n) ORDER BY i), '{}')
  FROM unnest(names) WITH ORDINALITY AS t(n, i)
$$;

-- Contract: ISO 8601 UTC text with second precision ('2026-03-01T00:00:00Z'),
-- the single timestamp format used in the graph mirror and API results. NULL in, NULL out.
CREATE FUNCTION kernel.iso(ts timestamptz) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
$$;

-- Contract: parses a date or timestamp given in a payload. A bare date means
-- midnight UTC; a timestamp without zone is read as UTC. Only ISO 8601 forms are
-- accepted: relative words ('now', 'yesterday') would resolve against the write time,
-- and the model must resolve them against the source. Anything else is a 'payload'
-- rejection naming the field.
CREATE FUNCTION kernel.parse_time(value text, field text) RETURNS timestamptz
LANGUAGE plpgsql STABLE AS $$
BEGIN
  IF value IS NULL THEN
    RETURN NULL;
  END IF;
  IF value ~ '^\d{4}-\d{2}-\d{2}$' THEN
    RETURN (value || 'T00:00:00Z')::timestamptz;
  END IF;
  IF value ~ '^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$' THEN
    RETURN (value || 'Z')::timestamptz;
  END IF;
  IF value ~ '^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}(:?\d{2})?)$' THEN
    RETURN value::timestamptz;
  END IF;
  PERFORM kernel.reject('payload', NULL, format('%s must be an ISO 8601 date or timestamp (YYYY-MM-DD)', field),
                        jsonb_build_object('field', field));
  RETURN NULL;
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow OR invalid_text_representation THEN
  PERFORM kernel.reject('payload', NULL, format('%s is not a valid date or timestamp', field),
                        jsonb_build_object('field', field));
  RETURN NULL;
END
$$;

-- Contract: aborts the current transaction with SQLSTATE WMK01 and a JSON detail
-- {"problem", "rule", "detail", ...extra}. The gateway turns this into an RFC 9457
-- problem document (gateway/problems.py). 'problem' is a rule category (types,
-- domain_range, cardinality, time, identity, provenance) or a write check
-- (payload, reference, duplicate, stale, agent).
CREATE FUNCTION kernel.reject(problem text, rule text, detail text, extra jsonb DEFAULT '{}')
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION USING
    ERRCODE = 'WMK01',
    MESSAGE = problem || ': ' || detail,
    DETAIL = (jsonb_build_object('problem', problem, 'rule', rule, 'detail', detail) || extra)::text;
END
$$;

-- Contract: trigger function that refuses UPDATE, DELETE and TRUNCATE. Attached to
-- every log table; the log is append-only for every role, including the owner.
CREATE FUNCTION kernel.forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION USING
    ERRCODE = 'WMK02',
    MESSAGE = format('%s on %s.%s is forbidden: the log is append-only', TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME);
END
$$;

-- Contract: confidence and trust levels as weights: high 1.0, medium 0.7, low 0.4. Pure.
CREATE FUNCTION kernel.level_weight(level text) RETURNS numeric
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE level WHEN 'high' THEN 1.0 WHEN 'medium' THEN 0.7 WHEN 'low' THEN 0.4 END
$$;

-- Contract: basis as a weight: observed 1.0, reported 0.7, inferred 0.4. Pure.
CREATE FUNCTION kernel.basis_weight(basis text) RETURNS numeric
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE basis WHEN 'observed' THEN 1.0 WHEN 'reported' THEN 0.7 WHEN 'inferred' THEN 0.4 END
$$;

-- Contract: basis strength order used by provenance rules: inferred 1 < reported 2 < observed 3. Pure.
CREATE FUNCTION kernel.basis_rank(basis text) RETURNS int
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE basis WHEN 'observed' THEN 3 WHEN 'reported' THEN 2 WHEN 'inferred' THEN 1 END
$$;

-- Contract: maps a payload confidence to a band. Raw model confidence is never used
-- directly: numbers map to high (>= 0.8), medium (>= 0.5) or low; band names pass
-- through; NULL means medium. Anything else is a 'payload' rejection.
CREATE FUNCTION kernel.confidence_band(value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF value IS NULL OR value = 'null'::jsonb THEN
    RETURN 'medium';
  ELSIF jsonb_typeof(value) = 'number' AND value::numeric BETWEEN 0 AND 1 THEN
    RETURN CASE WHEN value::numeric >= 0.8 THEN 'high' WHEN value::numeric >= 0.5 THEN 'medium' ELSE 'low' END;
  ELSIF value #>> '{}' IN ('low', 'medium', 'high') THEN
    RETURN value #>> '{}';
  END IF;
  PERFORM kernel.reject('payload', NULL, 'confidence must be low, medium, high or a number between 0 and 1',
                        jsonb_build_object('field', 'confidence'));
  RETURN NULL;
END
$$;
