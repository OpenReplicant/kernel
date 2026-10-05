-- 12_keys.sql
-- Erasure by key destruction (ADR 0022). Personal data is sealed (encrypted) when it is
-- written: a source with subjects under its own data key, the fields of a human agent under
-- the agent's key. The log and the sources keep the ciphertext forever. Destroying a key
-- (kernel.erase, 92_erase.sql) makes what it sealed unreadable everywhere, backups of the
-- log included. Keys live here, outside the log, and are the only kernel rows ever deleted.

SET ROLE kernel_owner;

CREATE TABLE kernel.data_keys (
  -- The source id or human agent id whose data the key seals.
  id         text PRIMARY KEY,
  -- The data subjects (agent ids) the key covers: erasing any of them destroys it.
  subjects   text[] NOT NULL CHECK (cardinality(subjects) BETWEEN 1 AND 64),
  key        bytea NOT NULL CHECK (length(key) = 32),
  created_at timestamptz NOT NULL
);
COMMENT ON TABLE kernel.data_keys IS
  'AES-256 data keys, one per sealed source and per human agent, with the subjects each covers. Only kernel.erase deletes rows. Back it up apart from the log, with a shorter retention.';
CREATE INDEX data_keys_subjects_idx ON kernel.data_keys USING gin (subjects);

CREATE TABLE kernel.erasures (
  id           text PRIMARY KEY,
  subject      text NOT NULL,
  -- The keys destroyed, the sealed sources among them, and the nodes re-projected.
  keys         text[] NOT NULL,
  sources      text[] NOT NULL,
  nodes        text[] NOT NULL,
  requested_by text NOT NULL CHECK (btrim(requested_by) <> ''),
  approved_by  text NOT NULL CHECK (btrim(approved_by) <> ''),
  reason       text,
  -- The head of the log when the keys were destroyed.
  at_offset    bigint NOT NULL CHECK (at_offset >= 0),
  recorded_at  timestamptz NOT NULL
);
COMMENT ON TABLE kernel.erasures IS
  'The erasure ledger: who was erased, which keys were destroyed, who asked and who approved. Written only by kernel.erase. Append-only.';
CREATE TRIGGER erasures_append_only BEFORE UPDATE OR DELETE ON kernel.erasures
  FOR EACH ROW EXECUTE FUNCTION kernel.forbid_change();
CREATE TRIGGER erasures_no_truncate BEFORE TRUNCATE ON kernel.erasures
  FOR EACH STATEMENT EXECUTE FUNCTION kernel.forbid_change();

-- Contract: creates data key p_id (a source id or a human agent id) covering p_subjects and
-- returns p_id. Volatile (32 random bytes): only the write functions call it, before an entry
-- is logged.
CREATE FUNCTION kernel.new_data_key(p_id text, p_subjects text[]) RETURNS text
LANGUAGE sql VOLATILE AS $$
  INSERT INTO kernel.data_keys (id, subjects, key, created_at)
  VALUES (p_id, p_subjects, public.gen_random_bytes(32), clock_timestamp())
  RETURNING id
$$;

-- Contract: p_plain sealed under data key p_key_id, as
-- 'wmk:sealed:<key id>:<base64 of a random 16-byte IV followed by the AES-256-CBC
-- ciphertext of the UTF-8 text>'. NULL in, NULL out. Volatile (a fresh IV each call): only
-- the write functions call it; the projection only unseals. Raises when the key is missing.
CREATE FUNCTION kernel.seal(p_plain text, p_key_id text) RETURNS text
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
  v_key bytea;
  v_iv bytea;
BEGIN
  IF p_plain IS NULL THEN
    RETURN NULL;
  END IF;
  SELECT key INTO v_key FROM kernel.data_keys WHERE id = p_key_id;
  IF v_key IS NULL THEN
    RAISE EXCEPTION 'no data key %', p_key_id;
  END IF;
  v_iv := public.gen_random_bytes(16);
  RETURN 'wmk:sealed:' || p_key_id || ':'
    || translate(encode(v_iv || public.encrypt_iv(convert_to(p_plain, 'UTF8'), v_key, v_iv, 'aes-cbc/pad:pkcs'),
                        'base64'), E'\n', '');
END
$$;

-- Contract: the text a kernel.seal value holds, or NULL when its key has been destroyed.
-- Anything that is not a sealed value comes back unchanged; callers apply it only to fields
-- they know are sealed. Deterministic for a given set of keys, so the projection may call
-- it. SECURITY DEFINER: readers open sealed text through the kernel's views and functions
-- without reading the keys themselves.
CREATE FUNCTION kernel.unseal(p_value text) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, kernel, public, pg_temp
AS $$
DECLARE
  v_key bytea;
  v_raw bytea;
BEGIN
  IF p_value IS NULL OR left(p_value, 11) <> 'wmk:sealed:' THEN
    RETURN p_value;
  END IF;
  SELECT key INTO v_key FROM kernel.data_keys WHERE id = split_part(p_value, ':', 3);
  IF v_key IS NULL THEN
    RETURN NULL;
  END IF;
  v_raw := decode(split_part(p_value, ':', 4), 'base64');
  RETURN convert_from(
    public.decrypt_iv(substring(v_raw FROM 17), v_key, substring(v_raw FROM 1 FOR 16), 'aes-cbc/pad:pkcs'), 'UTF8');
END
$$;

-- Contract: kernel.unseal for a sealed JSON document; NULL when its key has been destroyed.
CREATE FUNCTION kernel.unseal_json(p_value text) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT kernel.unseal(p_value)::jsonb
$$;
