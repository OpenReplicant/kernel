-- 35_packs.sql
-- Packs: the kernel's version, the record of installed packs, and kernel.install_pack,
-- which applies a pack's ontology manifest. The ontology is schema, not data (ADR 0002):
-- packs are installed at deploy time by the owner, never through kernel.write (ADR 0015).

SET ROLE kernel_owner;

-- Contract: the kernel's version (semantic versioning). Packs declare the kernel range
-- they support; the installer checks it. Pure.
CREATE FUNCTION kernel.version() RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT '0.5.0' $$;

CREATE TABLE kernel.packs (
  name          text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9-]*$' AND name <> 'core'),
  install_seq   bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
  version       text NOT NULL CHECK (version <> ''),
  kernel_range  text NOT NULL CHECK (kernel_range <> ''),
  manifest      jsonb NOT NULL CHECK (jsonb_typeof(manifest) = 'object'),
  manifest_hash text NOT NULL,
  installed_at  timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE kernel.packs IS
  'Installed packs: version, supported kernel range and the ontology manifest applied, in install order. Replay reinstalls these manifests.';

-- Contract: raises a pack installation error (SQLSTATE WMK03) with the message. Never returns.
CREATE FUNCTION kernel.pack_error(message text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '%', message USING ERRCODE = 'WMK03';
END
$$;

-- Contract: installs or updates one pack's ontology from its manifest
-- {"name", "version", "kernel", "namespaces"?, "kinds"?, "edge_kinds"?, "rules"?}, as
-- kernel/packs.py compiles it from the pack's schema.yaml and rules.yaml. Every entry is
-- recorded as defined_by the pack. Refuses (SQLSTATE WMK03): an entry the core or another
-- pack defines; a new node type for a kind or kernel edge for an edge kind; dropping an
-- entry the pack defined before; a rule id outside the pack's namespaces; rules naming
-- unknown edges, edge kinds, node types or kinds, or invalid patterns. The same manifest
-- installed again changes nothing. Returns {"pack", "version", "status", counts} with
-- status installed, updated or unchanged.
CREATE FUNCTION kernel.install_pack(p_manifest jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, kernel, public, pg_temp AS $$
DECLARE
  v_name text := p_manifest ->> 'name';
  v_hash text := encode(sha256(convert_to(p_manifest::text, 'UTF8')), 'hex');
  v_old kernel.packs;
  v_ns jsonb := coalesce(p_manifest -> 'namespaces', '[]');
  v_kinds jsonb := coalesce(p_manifest -> 'kinds', '[]');
  v_edge_kinds jsonb := coalesce(p_manifest -> 'edge_kinds', '[]');
  v_rules jsonb := coalesce(p_manifest -> 'rules', '[]');
  v_own text[];
  v_problem text;
  r jsonb;
  pattern text;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('kernel.install_pack', 0));
  IF jsonb_typeof(p_manifest) <> 'object' OR v_name IS NULL OR v_name !~ '^[a-z][a-z0-9-]*$' OR v_name = 'core' THEN
    PERFORM kernel.pack_error('a manifest needs a name: lower case letters, digits and hyphens, not core');
  END IF;
  SELECT string_agg(k, ', ') INTO v_problem FROM jsonb_object_keys(p_manifest) k
  WHERE k NOT IN ('name', 'version', 'kernel', 'namespaces', 'kinds', 'edge_kinds', 'rules');
  IF v_problem IS NOT NULL THEN
    PERFORM kernel.pack_error(format('%s: unknown manifest keys: %s', v_name, v_problem));
  END IF;
  IF coalesce(p_manifest ->> 'version', '') = '' OR coalesce(p_manifest ->> 'kernel', '') = '' THEN
    PERFORM kernel.pack_error(format('%s: a manifest needs a version and a kernel range', v_name));
  END IF;

  SELECT * INTO v_old FROM kernel.packs WHERE name = v_name;
  IF v_old.manifest_hash = v_hash THEN
    RETURN jsonb_build_object('pack', v_name, 'version', v_old.version, 'status', 'unchanged');
  END IF;
  v_own := ARRAY(SELECT x ->> 'name' FROM jsonb_array_elements(v_ns) x);

  -- Entries the core or another pack defines.
  SELECT format('%s %s is defined by %s', what, entry, owner) INTO v_problem FROM (
    SELECT 'namespace' AS what, t.name AS entry, t.defined_by AS owner
    FROM jsonb_array_elements(v_ns) x JOIN kernel.namespaces t ON t.name = x ->> 'name'
    UNION ALL
    SELECT 'kind', t.name, t.defined_by FROM jsonb_array_elements(v_kinds) x JOIN kernel.kinds t ON t.name = x ->> 'name'
    UNION ALL
    SELECT 'edge kind', t.name, t.defined_by
    FROM jsonb_array_elements(v_edge_kinds) x JOIN kernel.edge_kinds t ON t.name = x ->> 'name'
    UNION ALL
    SELECT 'rule', t.id, t.defined_by FROM jsonb_array_elements(v_rules) x JOIN kernel.rules t ON t.id = x ->> 'id'
  ) c WHERE owner <> v_name ORDER BY what, entry LIMIT 1;
  IF v_problem IS NOT NULL THEN
    PERFORM kernel.pack_error(format('%s: %s', v_name, v_problem));
  END IF;

  -- Changes that would reinterpret existing nodes and edges, and removals.
  SELECT problem INTO v_problem FROM (
    SELECT format('kind %s is a %s; it cannot become a %s', t.name, t.node_type, x ->> 'node_type') AS problem
    FROM jsonb_array_elements(v_kinds) x JOIN kernel.kinds t ON t.name = x ->> 'name'
    WHERE t.node_type <> x ->> 'node_type'
    UNION ALL
    SELECT format('edge kind %s specialises %s; it cannot specialise %s', t.name, t.edge, x ->> 'edge')
    FROM jsonb_array_elements(v_edge_kinds) x JOIN kernel.edge_kinds t ON t.name = x ->> 'name'
    WHERE t.edge <> x ->> 'edge'
    UNION ALL
    SELECT format('removing namespace %s is not supported', t.name) FROM kernel.namespaces t
    WHERE t.defined_by = v_name AND NOT t.name = ANY (v_own)
    UNION ALL
    SELECT format('removing kind %s is not supported', t.name) FROM kernel.kinds t
    WHERE t.defined_by = v_name AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_kinds) x WHERE x ->> 'name' = t.name)
    UNION ALL
    SELECT format('removing edge kind %s is not supported', t.name) FROM kernel.edge_kinds t
    WHERE t.defined_by = v_name AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_edge_kinds) x WHERE x ->> 'name' = t.name)
    UNION ALL
    SELECT format('removing rule %s is not supported', t.id) FROM kernel.rules t
    WHERE t.defined_by = v_name AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_rules) x WHERE x ->> 'id' = t.id)
  ) c ORDER BY problem LIMIT 1;
  IF v_problem IS NOT NULL THEN
    PERFORM kernel.pack_error(format('%s: %s', v_name, v_problem));
  END IF;

  -- References to the kernel's fixed vocabulary.
  SELECT problem INTO v_problem FROM (
    SELECT format('kind %s: %s is not a node type', x ->> 'name', x ->> 'node_type') AS problem
    FROM jsonb_array_elements(v_kinds) x WHERE NOT EXISTS (SELECT 1 FROM kernel.node_types WHERE name = x ->> 'node_type')
    UNION ALL
    SELECT format('kind %s: Claim kinds are modalities; packs add kinds of Entity, Agent or Event', x ->> 'name')
    FROM jsonb_array_elements(v_kinds) x WHERE x ->> 'node_type' = 'Claim'
    UNION ALL
    SELECT format('edge kind %s: %s is not a kernel edge', x ->> 'name', x ->> 'edge')
    FROM jsonb_array_elements(v_edge_kinds) x WHERE NOT EXISTS (SELECT 1 FROM kernel.edge_types WHERE name = x ->> 'edge')
    UNION ALL
    SELECT format('rule %s: ids start with one of the pack''s namespaces (%s)', x ->> 'id', array_to_string(v_own, ', '))
    FROM jsonb_array_elements(v_rules) x WHERE NOT split_part(x ->> 'id', '.', 1) = ANY (v_own)
    UNION ALL
    SELECT format('rule %s: namespace %s is not one of the pack''s', x ->> 'id', x ->> 'namespace')
    FROM jsonb_array_elements(v_rules) x WHERE x ->> 'namespace' IS NOT NULL AND NOT (x ->> 'namespace') = ANY (v_own)
  ) c ORDER BY problem LIMIT 1;
  IF v_problem IS NOT NULL THEN
    PERFORM kernel.pack_error(format('%s: %s', v_name, v_problem));
  END IF;

  INSERT INTO kernel.namespaces (name, label, description, defined_by)
  SELECT x ->> 'name', x ->> 'label', x ->> 'description', v_name FROM jsonb_array_elements(v_ns) x
  ON CONFLICT (name) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description;
  INSERT INTO kernel.kinds (name, node_type, label, description, defined_by)
  SELECT x ->> 'name', x ->> 'node_type', x ->> 'label', x ->> 'description', v_name FROM jsonb_array_elements(v_kinds) x
  ON CONFLICT (name) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description;
  INSERT INTO kernel.edge_kinds (name, edge, label, description, defined_by)
  SELECT x ->> 'name', x ->> 'edge', x ->> 'label', x ->> 'description', v_name FROM jsonb_array_elements(v_edge_kinds) x
  ON CONFLICT (name) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description;
  INSERT INTO kernel.rules (id, category, namespace, params, label, description, defined_by)
  SELECT x ->> 'id', x ->> 'category', x ->> 'namespace', x -> 'params', x ->> 'label', x ->> 'description', v_name
  FROM jsonb_array_elements(v_rules) x
  ON CONFLICT (id) DO UPDATE SET category = EXCLUDED.category, namespace = EXCLUDED.namespace,
    params = EXCLUDED.params, label = EXCLUDED.label, description = EXCLUDED.description;

  -- Rules may name only edges, edge kinds, node types and kinds that now exist.
  SELECT problem INTO v_problem FROM (
    SELECT format('rule %s: %s is not a kernel edge', x ->> 'id', x -> 'params' ->> 'edge') AS problem
    FROM jsonb_array_elements(v_rules) x
    WHERE x -> 'params' ? 'edge' AND NOT EXISTS (SELECT 1 FROM kernel.edge_types WHERE name = x -> 'params' ->> 'edge')
    UNION ALL
    SELECT format('rule %s: %s is not an edge kind of %s', x ->> 'id', x -> 'params' ->> 'kind', x -> 'params' ->> 'edge')
    FROM jsonb_array_elements(v_rules) x
    WHERE x ->> 'category' IN ('domain_range', 'cardinality') AND x -> 'params' ? 'kind'
      AND NOT EXISTS (SELECT 1 FROM kernel.edge_kinds WHERE name = x -> 'params' ->> 'kind' AND edge = x -> 'params' ->> 'edge')
    UNION ALL
    SELECT format('rule %s: %s is not a node type', x ->> 'id', t)
    FROM jsonb_array_elements(v_rules) x,
         LATERAL (SELECT x -> 'params' ->> 'node_type' UNION ALL
                  SELECT jsonb_array_elements_text(coalesce(x -> 'params' -> 'from' -> 'types', '[]')) UNION ALL
                  SELECT jsonb_array_elements_text(coalesce(x -> 'params' -> 'to' -> 'types', '[]'))) AS n(t)
    WHERE t IS NOT NULL AND NOT EXISTS (SELECT 1 FROM kernel.node_types WHERE name = t)
    UNION ALL
    SELECT format('rule %s: %s is not a kind', x ->> 'id', k)
    FROM jsonb_array_elements(v_rules) x,
         LATERAL (SELECT jsonb_array_elements_text(coalesce(x -> 'params' -> 'kinds', '[]')) UNION ALL
                  SELECT jsonb_array_elements_text(coalesce(x -> 'params' -> 'key_kinds', '[]')) UNION ALL
                  SELECT jsonb_array_elements_text(coalesce(x -> 'params' -> 'from' -> 'kinds', '[]')) UNION ALL
                  SELECT jsonb_array_elements_text(coalesce(x -> 'params' -> 'to' -> 'kinds', '[]'))) AS n(k)
    WHERE NOT EXISTS (SELECT 1 FROM kernel.kinds WHERE name = k)
      AND k NOT IN ('descriptive', 'predictive', 'normative', 'proposed', 'hypothetical')
  ) c ORDER BY problem LIMIT 1;
  IF v_problem IS NOT NULL THEN
    PERFORM kernel.pack_error(format('%s: %s', v_name, v_problem));
  END IF;
  FOR r IN SELECT x FROM jsonb_array_elements(v_rules) x WHERE x -> 'params' ? 'patterns' LOOP
    FOR pattern IN SELECT value FROM jsonb_each_text(r -> 'params' -> 'patterns') LOOP
      BEGIN
        PERFORM '' ~ pattern;
      EXCEPTION WHEN invalid_regular_expression THEN
        PERFORM kernel.pack_error(format('%s: rule %s: %s is not a valid pattern', v_name, r ->> 'id', pattern));
      END;
    END LOOP;
  END LOOP;

  INSERT INTO kernel.packs (name, version, kernel_range, manifest, manifest_hash)
  VALUES (v_name, p_manifest ->> 'version', p_manifest ->> 'kernel', p_manifest, v_hash)
  ON CONFLICT (name) DO UPDATE SET version = EXCLUDED.version, kernel_range = EXCLUDED.kernel_range,
    manifest = EXCLUDED.manifest, manifest_hash = EXCLUDED.manifest_hash, updated_at = now();

  RETURN jsonb_build_object(
    'pack', v_name, 'version', p_manifest ->> 'version',
    'status', CASE WHEN v_old.name IS NULL THEN 'installed' ELSE 'updated' END,
    'namespaces', jsonb_array_length(v_ns), 'kinds', jsonb_array_length(v_kinds),
    'edge_kinds', jsonb_array_length(v_edge_kinds), 'rules', jsonb_array_length(v_rules));
END
$$;

RESET ROLE;
