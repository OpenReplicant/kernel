# 0015. Packs declare their ontology; the kernel installs it

Date: 2026-10-04 · Status: accepted

## Context

Packs applied raw SQL that inserted into the kernel's ontology tables, every pack went
into every database, and pack tests, fixtures and servers lived in the kernel's folders.
That tied each pack to the kernel's table layout and stood in the way of keeping packs in
their own repositories (a packs monorepo first, a repository per pack later).

## Decision

- **Manifests.** A pack is a folder whose root is an Agent Skill. `SKILL.md` names it and
  carries `metadata.version` and `metadata.kernel` (the kernel range it supports);
  `schema.yaml` declares namespaces, kinds and edge kinds; `rules.yaml` declares rules in
  the kernel's rule categories. Pack SQL is refused until packs get their own schema and
  role.
- **Installation.** `kernel/packs.py` validates a folder and checks its range against
  `kernel.version()`; `kernel.install_pack(manifest)` applies it as the owner and records
  it in `kernel.packs`. It refuses entries the core or another pack defines, rule ids
  outside the pack's namespaces, references to unknown edges, kinds or node types, invalid
  patterns, and upgrades that drop an entry or change a kind's node type or an edge kind's
  edge. Reinstalling the same manifest changes nothing.
- **Choice per database.** The stack's `packs` service installs `$WMK_PACKS` (default: every
  pack) before the gateway starts; the database image holds no pack. Test databases get
  the packs a test folder names (`wmk_packs`), eval databases those a fixture names.
- **Replay** reinstalls the manifests the live database recorded, in install order, and
  compares the ontology too.
- **Self-contained packs.** A pack's tests, fixtures, live scenarios and servers live in
  its folder; servers are uv workspace members with their own `pyproject.toml`. The
  kernel's test kit is the `kernel.testing` pytest plugin.
- The kernel is version 0.2.0; packs that need the installer declare `>=0.2`.

## Consequences

A pack no longer depends on the kernel's tables, only on the manifest format and the
seven tools, so it can move to another repository with the kernel as a dependency. The
installer has no registry, fetching or approval step yet; those stay out of scope.
Removing or retyping a pack's terms needs a migration story before it is allowed.
