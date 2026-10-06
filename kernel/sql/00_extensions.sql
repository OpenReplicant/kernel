-- 00_extensions.sql
-- Extensions, the kernel schema and the five group roles.
-- Applied as a superuser; every later file switches to kernel_owner.

CREATE EXTENSION IF NOT EXISTS age;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- Sealing personal data so it can be erased by destroying its key (ADR 0022).
CREATE EXTENSION IF NOT EXISTS pgcrypto SCHEMA public;

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
  -- An operator carrying out an approved erasure (kernel.erase); never an agent.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kernel_eraser') THEN
    CREATE ROLE kernel_eraser NOLOGIN;
  END IF;
  -- A signed-in person deciding on proposals (kernel.decide, ADR 0029); never an agent, and
  -- no gateway login holds it.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kernel_approver') THEN
    CREATE ROLE kernel_approver NOLOGIN;
  END IF;
END
$$;
-- A signed-in person reads what any reader reads.
GRANT kernel_reader TO kernel_approver;

-- kernel.resolve_candidates pins the trigram threshold with a SET clause. In a session that
-- has not loaded pg_trgm yet the setting is an unknown placeholder, which only a superuser
-- or a holder of this privilege may attach to a function.
GRANT SET ON PARAMETER pg_trgm.similarity_threshold TO kernel_owner;

CREATE SCHEMA IF NOT EXISTS kernel AUTHORIZATION kernel_owner;
COMMENT ON SCHEMA kernel IS
  'World Model Kernel: append-only log, its graph projection, ontology, the three write functions and erasure.';

-- Functions are executable by PUBLIC by default; the kernel grants EXECUTE explicitly.
ALTER DEFAULT PRIVILEGES FOR ROLE kernel_owner IN SCHEMA kernel REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

GRANT USAGE ON SCHEMA ag_catalog TO kernel_owner, kernel_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA ag_catalog TO kernel_owner, kernel_reader;
