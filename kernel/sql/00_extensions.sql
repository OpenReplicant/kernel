-- 00_extensions.sql
-- Extensions, the kernel schema and the three group roles.
-- Applied as a superuser; every later file switches to kernel_owner.

CREATE EXTENSION IF NOT EXISTS age;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Group roles are cluster-wide; databases created for tests and replay share them.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kernel_owner') THEN
    CREATE ROLE kernel_owner NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kernel_writer') THEN
    CREATE ROLE kernel_writer NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kernel_reader') THEN
    CREATE ROLE kernel_reader NOLOGIN;
  END IF;
END
$$;

-- kernel.resolve_candidates pins the trigram threshold with a SET clause. In a session that
-- has not loaded pg_trgm yet the setting is an unknown placeholder, which only a superuser
-- or a holder of this privilege may attach to a function.
GRANT SET ON PARAMETER pg_trgm.similarity_threshold TO kernel_owner;

CREATE SCHEMA IF NOT EXISTS kernel AUTHORIZATION kernel_owner;
COMMENT ON SCHEMA kernel IS
  'World Model Kernel: append-only log, its graph projection, ontology and the three write functions.';

-- Functions are executable by PUBLIC by default; the kernel grants EXECUTE explicitly.
ALTER DEFAULT PRIVILEGES FOR ROLE kernel_owner IN SCHEMA kernel REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

GRANT USAGE ON SCHEMA ag_catalog TO kernel_owner, kernel_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA ag_catalog TO kernel_owner, kernel_reader;
