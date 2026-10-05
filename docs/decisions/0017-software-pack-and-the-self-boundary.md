# 0017. The software pack, its adapter and the self boundary

Date: 2026-10-04 · Status: accepted

## Context

The kernel's first self-model is of software: this repository, the stack it runs as, and
the agent that writes through it. Devops needs the same map (what runs, what it depends
on, what changed), so the vocabulary is a pack rather than something special to the self.
Much of it is declared in files, which a deterministic adapter can read without a model.

## Decision

- **The software pack** (`packs/software/`) adds the `software` namespace: `system`,
  `repository`, `package`, `image`, `service`, `stack`, `endpoint`, `volume`, `network`,
  `host`, `pipeline` and the `deployment` event, and eleven edge kinds that specialise
  `depends_on`, `part_of`, `responsible_for` and `monitors`. Identity keys: package URLs
  for packages and images, the URL for repositories, `<repository URL>#<where>` for what a
  repository defines, commit hashes for changes.
- **Versions live on edges.** A package or image is one node; the edge that uses it carries
  `props.version`, `props.tag` or `props.digest`. Props are part of an edge's identity, so
  an upgrade is a new edge and the old one is retracted.
- **The adapter is a gateway client.** `wmk-software` (a uv workspace member) reads
  `pyproject.toml`, the compose file and the Dockerfiles it builds, CI workflows and the
  first-parent git history, and writes one observed claim per fact through the gateway's
  tools. It holds no database credentials. Its plan renders as an eval fixture script, so
  `make eval` scores the mapping against a graph written by hand.
- **A file is a collection.** Each file is a source whose `collection` is
  `<repository URL>#<path>`: every version of it counts as one source, so a newer version
  supersedes the older one under belief v1. An unchanged file is skipped. When a file
  changes, the edges an earlier version asserted and the new one does not are retracted by
  a negative assertion from the same collection. History is append-only and never
  retracted. `kernel.query_log` gains a `collection` filter for this (a read).
- **The self is a set of claims.** A `system` node stands for this kernel instance; its parts
  are `part_of` edges into it, each an inferred claim with its own confidence: the stack and
  the repository high, the writing agent medium, CI pipelines low. Where the self ends is
  then belief, which can be contested and revised like any other.
- The eval player learns two templates: `{{at:<source>:<offset>}}` (the chunk holding a
  character) and `{{agent:self}}` (the agent writing through the gateway).

## Consequences

The adapter maps what files declare, not what runs; observing the running stack (compose
state, OTel) is a separate source that can contest it. Python versions are declared ranges
until the lock file is read. A repository without git is not mapped. Changing the self
boundary, like any change to the system, stays a proposal for people to approve (the
approval-channel ADR, next).

## Amendment (2026-10-04)

The adapter no longer retracts with its own reading of the log: it maps each changed file
in an extraction run, and closing the run makes the kernel retract what an older run over
the file found and this one did not ([ADR 0020](0020-quotes-and-extraction-runs.md)). A run
with a refused claim is cancelled, so a partial pass retracts nothing.

## Amendment (2026-10-04, later)

The adapter (0.3.0) also maps Kubernetes manifests and OpenAPI documents, found by content
anywhere in the repository. A namespace is a stack. A workload is a service that runs its
containers' images and mounts its volume claims. Service ports and Ingress rules are
endpoints of the workloads they reach. An OpenAPI operation is an endpoint of the service
named in `x-service`. Every file declares the repository as its origin (ADR 0021).
