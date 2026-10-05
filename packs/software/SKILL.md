---
name: software
description: >
  Software pack for the World Model Kernel: map repositories, packages, container images,
  services, stacks, endpoints and CI pipelines, the changes that happen to them, and the
  boundary of a system's self. Use with the core skill when asked how a system is built or
  run, what depends on what, what a change would affect, or what this system is made of.
metadata:
  version: "0.1.0"
  kernel: ">=0.2 <1.0"
  namespace: software
  requires: world-model-core
---

# Software (pack)

Repositories, packages, images, services and the rest go in the `software` namespace.
Versions belong on edges, not nodes: a package or image is one node however many versions
of it are in use, and each edge that uses it says which (`props.version`, `props.tag`,
`props.digest`). An upgrade is then a new edge, and the old one is retracted.

## Map a repository with the adapter

The adapter reads the files that say how a repository is built and run and writes what
each one states as observed claims citing it, through the gateway:

```sh
uv run wmk-software plan .                       # print the claims it would write
uv run wmk-software map . --url http://localhost:8000/mcp
uv run wmk-software map . --self "World Model Kernel (this instance)"   # also the self boundary
```

It maps `pyproject.toml` (and uv workspace members), the compose file and the Dockerfiles
it builds, `.github/workflows/*.yml`, Kubernetes manifests, OpenAPI documents and the
first-parent git history.

- **Kubernetes:** a namespace is a `stack`. A Deployment, StatefulSet, DaemonSet, Job or
  CronJob is a `service` named `<namespace>/<name>`, which `runs` its containers' images
  and `mounts` its volume claims. A Service's ports and an Ingress's rules are endpoints of
  the workloads their selectors reach.
- **OpenAPI:** each operation is an `endpoint` named `<API title> <METHOD> <path>`. It is
  `exposed_by` the service named in the document's `x-service` (`<stack>/<service>`), else
  by a service of the API's own.

Each file is one source whose `collection` is `<repository URL>#<path>`, so a newer version
of a file supersedes the older one. Every file's origin is the repository, so its files do
not count as independent evidence for each other. Mapping again writes nothing for
unchanged files, reuses nodes by identity key, and retracts edges a changed file no longer
states. Run it after a change lands; it never needs database credentials.

Use the adapter for what files declare. Map by hand, with the core skill, what only people
or running systems know: who owns a service, why it was built that way, what an incident
showed, what actually runs on a host.

## Map it by hand

| Fact | Operations |
| --- | --- |
| A repository | `repository`, `identity` `{"url": ...}` |
| A package (library, GitHub Action) | `package`, `identity` `{"purl": "pkg:pypi/psycopg"}` without a version; `uses_package` from what depends on it, with `props.constraint` or `props.version` |
| A container image | `image`, `identity` `{"purl": "pkg:docker/postgrest/postgrest"}` for registry images; `runs` from a service with `props.tag` or `props.digest`; `based_on` its base image; `built_from` the repository |
| A deployable unit of a stack | `service` named `<stack>/<service>`, `part_of` the `stack` |
| Start order | `needs` from a service to the one it waits for, `props.condition` |
| Where it listens | `endpoint` named `<stack>/<service>:<port>`, `exposed_by` the service, `props.published` |
| Storage, networks, machines | `volume` (`mounts`), `network` (`attached_to`), `host` (`runs_on`) |
| A CI job | `pipeline` named `<workflow>/<job>`, `checks` the repository, `uses_package` its actions |
| A merge, release or config change | Event `change` with `start` and `identity` `{"commit": ...}`; the repository or service `participates_in` it (`props.role`) |
| A rollout | Event `deployment`; the service and image `participates_in` it |
| An outage | Event `incident` (core); affected services `participates_in` it |
| Who owns or runs it | core `responsible_for` from a person or team |

Stacks, services, endpoints, volumes, networks and pipelines carry `identity`
`{"defined_at": "<repository URL>#<where>"}`, so two stacks that both call a service `db`
stay apart.

## Declared and observed

A compose file says what should run; `docker ps`, a deploy log or OTel says what does.
Keep both: claims from files are what the file states (basis `observed`, citing the file),
claims from a running system cite that observation. Where they disagree, the edge is
contested and both stay on record. That gap, intended against actual, is often the most
useful thing to show.

## The self

A system's self is a `system` node and the `part_of` edges into it. Each part is its own
claim with its own belief, so the boundary can be fuzzy and can move: the stack and the
repository are clearly part of this kernel instance; the agent writing through the gateway
is part of it; a CI pipeline that runs elsewhere is, with low confidence. When you find a
new part, or decide something is not a part, write a claim saying why (basis `inferred`)
rather than editing the boundary silently. Proposals to change the self go through the
same proposal and approval path as any change; nothing here acts on the system.

## Questions about a system

What a service needs, transitively (blast radius runs the other way):

```cypher
MATCH (s:Entity {kind: 'service', name: $service})-[:depends_on*1..4]->(d)
RETURN DISTINCT d.kind, d.name
```

What breaks if an image or package changes:

```cypher
MATCH (x {id: $node})<-[:depends_on*1..4]-(u)
RETURN DISTINCT u.kind, u.name
```

Every image in use and where it comes from:

```cypher
MATCH (s:Entity {kind: 'service'})-[r:depends_on {kind: 'runs'}]->(i:Entity {kind: 'image'})
OPTIONAL MATCH (i)-[b:depends_on {kind: 'based_on'}]->(base)
RETURN s.name, i.name, r.props, base.name, b.props
```

What the system is made of, with how sure the kernel is of each part:

```cypher
MATCH (p)-[e:part_of]->(sys:Entity {kind: 'system'})
RETURN sys.name, p.kind, p.name, e.belief_status, e.belief_score
```

Recent changes to a repository:

```cypher
MATCH (r:Entity {kind: 'repository'})-[:participates_in]->(c:Event {kind: 'change'})
RETURN c.name, c.props ORDER BY c.name
```

## Rules this pack adds

- `software.allowed_kinds`: the kinds above, plus core `component`, `data`, `concept`,
  `change`, `incident` and `occurrence`.
- One `domain_range` rule per edge kind: each connects only the kinds in the table above.
- `software.purl`, `software.repository_url`, `software.definition_key`,
  `software.system_instance`, `software.commit`: identity keys; a second node with the
  same key is refused as a duplicate.

The ontology is declared in `schema.yaml` and `rules.yaml` and applied by the kernel's pack
installer.
