# 0028. What runs, observed: Docker, drift and the self

Date: 2026-10-06 · Status: accepted

## Context

Phase 3, item 2 (ADR 0026) extends the map of software from what a repository declares
to what runs. The software pack's adapter already maps the declared view: compose files,
Kubernetes manifests, Dockerfiles, CI and git history (ADR 0017). Drift is that view
contradicted by an observation of the running system. The kernel's own stack runs on
Docker Compose, and so will the Northwind demo, so the system can observe itself first.

## Decision

**No new pack.** The software pack's ontology already covers what runs:
- services, images, endpoints and hosts;
- `runs` with the tag or digest in use, `exposed_by` with the published address,
  `runs_on`;
- `deployment` events and core `incident` events.

A separate sysops pack would have to depend on these terms, and packs have no
dependencies (there is no registry). The pack gains one identity rule,
`software.runtime_key`, for events observed at runtime, and moves to 0.2.0. The
observation code lives in `wmk-software` beside the repository adapter.

**Observation is deterministic and comes in three steps.**
1. **`capture`** reads the Docker Engine API over its socket, for the containers of one
   Compose project. It keeps only an allowlist of fields:
   - id, name, creation time;
   - image reference and image id;
   - the Compose labels;
   - state, health, restart count;
   - published ports.

   Environment variables, commands, mounts and other labels are never captured: they hold
   secrets. The capture is a JSON file with the host name and the time of observation. It
   is the recorded input that tests and fixtures replay.
2. **`observe`** maps a capture without reading the model, like `discover` (ADR 0027).
   It writes two sources, both with the host as origin.
   - **The state now**, mapped in an extraction run, so the next capture retracts what no
     longer runs:
     - each service with a container `part_of` its stack;
     - `runs` its image, with the same props the declared view uses (tag or digest);
     - its published ports, `exposed_by` with the same props;
     - `runs_on` the host.
   - **The history**, append-only like git history: a `deployment` per container, with
     the service and image taking part, at the container's creation time.

   A container that finished with code 0 counts as having run its image: Compose jobs,
   such as this stack's pack installer, exit when done.

   Observed nodes reuse the declared ones by name. With `--repo`, they also carry the
   declared identity keys (`<repository URL>#<project>/<service>`). Map the repository
   first: a registry image is declared with a package URL, and an observation made first
   would not carry it.
3. **`drift`** reads the declared view of the project's stack through `query_graph` and
   records one observed verdict on each declared edge it can check, by `edge_id`, as
   `conform` does. Each verdict is an assertion or a denial.
   - A declared `runs`: asserted when the service's container runs that image with that
     tag or digest; denied when it runs another, or no container runs.
   - A declared published port: asserted when it is published there; denied otherwise.
   - A service that starts only with a Compose profile and has no container is not
     checked.
   - A job (a service others wait for to complete successfully) whose container finished
     cleanly counts as running.

   Drift also owns incidents, because closing one needs what the kernel already holds. It
   opens an `incident` for each container that is unhealthy, restarting or exited with an
   error, keyed by `software.runtime_key`. It moves an ongoing incident to `completed` when
   the service's containers run cleanly again. Opening and closing cite one append-only
   source, so the newer status supersedes the older. Two sources would disagree, and the
   status would read as contested.

   Where the repository and the observation disagree, the edge is contested: that is
   drift, shown like any other disagreement.

**The self.** `make observe-self` captures the kernel's own stack and runs `observe` and
`drift`, after `make map-self`. The self-model then holds three things:
- what the repository declares;
- what runs;
- where the two differ.

Each has its own belief. CI runs it on every change.

**Later readers, same model.** A capture is a list of observed containers. Kubernetes
(pods and their workloads), Prometheus or Grafana (health beyond a container's
healthcheck) and the forge (deployments and releases) add readers that produce it, one per
system. The forge comes with the approval channel (ADR 0026, item 3).

## Consequences

The system can say what it is running, whether that matches what it declares, and what
has happened to it, from observation rather than assertion. The kernel itself does not
change.

Not in this step:
- readers other than Docker;
- metrics;
- observing hosts beyond their name;
- acting on drift. Acting is a proposal through the approval channel (invariant 10),
  never something an observation does.
