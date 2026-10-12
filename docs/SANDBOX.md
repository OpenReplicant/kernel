# The sandbox (S4)

Untrusted code (code a model wrote, external code, benchmark tests) runs only in the sandbox:
a fresh container per run, behind an MCP server. Gate: `tests/test_sandbox.py`.

```
chain ── sandbox_run node ──MCP (stdio or HTTP)──▶ sandbox server ──▶ backend: docker | local
             │                                     one tool, fixed       (podman, kubernetes,
             ▼                                     security defaults      nomad: later)
     sandbox_run source in the graph
```

## Why our own MCP server

The runtime is meant to be swappable behind an MCP interface. The MCP servers that exist for
Docker, Podman, Kubernetes and Nomad (surveyed in `docs/SPIKE_NOTES.md`, S4) don't fit:

- their "run" tools don't apply no-network, read-only root, limits or a non-root user, or
  apply them only through a raw passthrough (compose files, manifests, job specs, CLI args)
  that is also full control of the runtime (on Docker, host root);
- none runs to completion and returns the exit code and output;
- each has its own tools and parameters, so swapping runtime would mean changing chains.

So the interface is one tool on a server of ours, and a runtime is a backend of that server
(or another server with the same tool).

## The tool

```
sandbox_run(command: [str], files: {path: text}, image: "python",
            timeout_s, cpus, memory, pids, max_output)            # limits optional
→ {exit_code (null on timeout), stdout, stderr, timed_out, duration_s,
   backend, isolated, image, limits, container?}
```

Fixed by the server, not the caller (the tool takes no network, mount, user or flag
arguments):

| | docker backend |
|---|---|
| network | none (`--network=none`) |
| filesystem | read-only root; `/tmp` a 64 MB tmpfs; the files read-only at `/work` |
| user | uid 10001, no capabilities, no new privileges |
| limits | CPU, memory (= memory + swap), pids; defaults and caps from the config |
| time | at the timeout the container is removed (`docker rm -f`) |
| environment | nothing from the host (the image's own variables only) |
| images | an allowlist by alias (`python` → `mirror.gcr.io/library/python:3.12-slim`); runs never pull |

`local` backend (the sandbox switched off): a host subprocess in a temporary directory with an
empty environment (`PATH`, `HOME`), an address-space and a CPU-time limit, killed at the
timeout. **Not isolated**: it can reach the network and the host's files. Every result says
`isolated: false`, and so does its record.

## Config

`config/kernel.yaml`, section `sandbox` (docs/CONFIG.md):

```yaml
sandbox:
  enabled: true         # false → the local backend
  backend: docker
  server: {command: ["${python}", "-m", "sandbox.server"]}   # or {url: http://host:port/mcp}
  engine: docker        # podman takes the same flags
  images: {python: mirror.gcr.io/library/python:3.12-slim}
  defaults: {timeout_s: 10, cpus: 1.0, memory: 256m, pids: 64, max_output: 1048576}
  max: {timeout_s: 300, cpus: 2.0, memory: 1g, pids: 256, max_output: 8388608}
```

`tools/dev.sh up` pulls the images.

## The node

`kernel/nodes/sandbox_run.py` (x/python):

    in:  {"sandbox_run": {command, files, image?, timeout_s?, …, inputs?}, ...}
    out: {"sandbox_result": {...the tool's result, "source": "<sandbox_run source id>"}, ...}

It starts the configured server over stdio (or connects to its URL). A stdio server gets
only `HOME`, `LC_CTYPE`, `PATH`, `SHELL` and `TERM` from the node's environment (the MCP Python
SDK's default, checked: `docs/SPIKE_NOTES.md`, S4), plus the config file's path. Every run is
recorded as a `sandbox_run` source: the request (command, files, limits), `inputs` (what the
run is for, e.g. the model call that wrote the code), the result, the config file and the
server. Code that fails or times out is an ordinary result; a run the server refuses (an image
not on the list, a bad path) or can't start is recorded with `status: refused` and the node
fails.

A claim made from a run cites it: `tests/modules/coder` asks Haiku for a function, runs the
task's tests on it in the sandbox, and writes `coder.passed` (`observed`, source the run, span
`$.result.exit_code`); the run's `inputs.model_call` is the call that wrote the code.

## Choosing a runtime

Docker is what this environment has; its daemon runs as root, and the sandbox's isolation is
runc's (namespaces, seccomp's default profile, cgroups). For anything beyond development use
a runtime with a smaller blast radius: rootless Podman (same flags: `engine: podman`), gVisor
(`runsc`) or Kata under Docker or Kubernetes, or Kubernetes / Nomad jobs on separate nodes.
Each is a backend to add to `sandbox/backends.py` with the same guarantees (BACKLOG).

## Known limits

- The docker backend mounts a host temporary directory, so the server and the Docker daemon
  must share a filesystem (true for a local daemon). Remote runtimes need the files sent
  another way (a Kubernetes ConfigMap, a Nomad artifact).
- One server process per run over stdio (about 1 s with the container); a long-lived server
  over streamable HTTP avoids the start-up.
- Output is capped at `max_output` bytes per stream; the rest is counted, not kept.
