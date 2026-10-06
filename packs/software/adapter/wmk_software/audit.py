"""Changes without approval (ADR 0031): every first-parent change of a repository, judged
from what the kernel holds, and the deployments of changes nobody approved.

`read` gathers the facts through the gateway's `query_graph`; `judge` decides, purely, so
the same graph always gives the same audit. A change is:
- **decided**: an approved kernel proposal names its pull request in `props.change`
  (`{"kind": "pull_request", "url": ..., "head"?: ...}`), with the merged head if it names one;
- **reviewed**: merged through a pull request that a person other than its author approved
  before the merge, on the merged head;
- **stale**: the only approval was of an earlier commit, on the forge or by a proposal;
- **unreviewed**: merged through a pull request with no approval;
- **no pull request known**: no pull request the kernel knows merged it.

Machines never count as approvers. The audit writes nothing: it is a query.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from wmk_software.repo import repo_name

DECIDED = "decided"
REVIEWED = "reviewed"
STALE = "stale"
UNREVIEWED = "unreviewed"
NO_PULL = "no pull request known"
STATUSES = (DECIDED, REVIEWED, STALE, UNREVIEWED, NO_PULL)
APPROVED = (DECIDED, REVIEWED)
# A deployment's change that is not in the repository's mapped history.
UNMERGED = "not in the mapped history"
NO_REVISION = "revision unknown"


class AuditError(Exception):
    """The gateway refused a read, or the repository is not in the kernel."""


@dataclass
class Approval:
    agent: str
    kind: str
    name: str
    commit: str | None


@dataclass
class Pull:
    id: str
    name: str
    number: int | None
    url: str | None
    author: str | None = None
    merger: str | None = None
    merger_name: str | None = None
    head: str | None = None  # the merged head, from the merge
    approvals: list[Approval] = field(default_factory=list)


@dataclass
class Change:
    id: str
    name: str
    commit: str | None
    offset: int


@dataclass
class Facts:
    """What the kernel holds about one repository, as `read` found it."""

    repository: str
    changes: list[Change] = field(default_factory=list)
    pulls: dict[str, Pull] = field(default_factory=dict)
    merged: dict[str, str] = field(default_factory=dict)  # change id -> pull id
    proposals: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # approved: id, change
    deployments: list[tuple[str, str]] = field(default_factory=list)  # of the repository's images
    revisions: dict[str, tuple[str, str | None]] = field(default_factory=dict)  # deployment -> change, commit


@dataclass
class Verdict:
    change: str
    name: str
    commit: str | None
    status: str
    reason: str
    pull: int | None = None


@dataclass
class Deployed:
    deployment: str
    name: str
    status: str
    change: str | None = None
    commit: str | None = None


@dataclass
class Audit:
    repository: str
    changes: list[Verdict]
    deployments: list[Deployed]

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(STATUSES, 0)
        for v in self.changes:
            out[v.status] += 1
        return out

    def unapproved(self) -> list[Verdict]:
        return [v for v in self.changes if v.status not in APPROVED]

    def deployed_without_approval(self) -> list[Deployed]:
        return [d for d in self.deployments if d.status not in (*APPROVED, NO_REVISION)]

    def to_json(self) -> str:
        return (
            json.dumps({"repository": self.repository, "counts": self.counts(), **asdict(self)}, indent=2)
            + "\n"
        )

    def text(self) -> str:
        counts = ", ".join(f"{n} {s}" for s, n in self.counts().items() if n)
        lines = [f"Changes to {repo_name(self.repository)}: {len(self.changes)} ({counts or 'none'})"]
        for v in self.changes:
            sha = (v.commit or "")[:7] or "-------"
            lines.append(f"  {v.status:<21} {sha}  {v.name}: {v.reason}")
        bad = self.deployed_without_approval()
        unknown = sum(d.status == NO_REVISION for d in self.deployments)
        lines.append(
            f"Deployments of its images: {len(self.deployments)}, {len(bad)} without approval, "
            f"{unknown} with no known revision"
        )
        for d in bad:
            lines.append(f"  {d.status:<21} {(d.commit or '')[:7]:<7}  {d.name}")
        return "\n".join(lines) + "\n"


def _pull_url_of(change: dict[str, Any]) -> str | None:
    if change.get("kind") != "pull_request":
        return None
    url = change.get("url")
    return str(url).rstrip("/") if url else None


def judge(facts: Facts) -> Audit:
    """The audit of what `read` found. Pure: the same facts give the same audit."""
    by_url = {p.url.rstrip("/"): p for p in facts.pulls.values() if p.url}
    decisions: dict[str, list[str | None]] = defaultdict(list)  # pull id -> heads the proposals name
    for _, change in facts.proposals:
        url = _pull_url_of(change)
        if url in by_url:
            head = change.get("head")
            decisions[by_url[url].id].append(str(head) if head else None)

    verdicts: list[Verdict] = []
    status_of: dict[str, Verdict] = {}
    for change in sorted(facts.changes, key=lambda c: c.offset, reverse=True):
        pull = facts.pulls.get(facts.merged.get(change.id, ""))
        verdict = _judge_change(change, pull, decisions.get(pull.id, []) if pull else [])
        verdicts.append(verdict)
        status_of[change.id] = verdict

    deployed: list[Deployed] = []
    for dep_id, name in sorted(facts.deployments, key=lambda d: d[1]):
        change_id, commit = facts.revisions.get(dep_id, (None, None))
        if change_id is None:
            deployed.append(Deployed(dep_id, name, NO_REVISION))
        elif change_id in status_of:
            deployed.append(Deployed(dep_id, name, status_of[change_id].status, change_id, commit))
        else:
            deployed.append(Deployed(dep_id, name, UNMERGED, change_id, commit))
    return Audit(facts.repository, verdicts, deployed)


def _judge_change(change: Change, pull: Pull | None, heads: list[str | None]) -> Verdict:
    def verdict(status: str, reason: str) -> Verdict:
        return Verdict(change.id, change.name, change.commit, status, reason, pull.number if pull else None)

    if pull is None:
        return verdict(NO_PULL, "no pull request the kernel knows merged it")
    ref = f"#{pull.number}" if pull.number is not None else pull.name
    if any(h is None or h == pull.head for h in heads):
        return verdict(DECIDED, f"an approved proposal names {ref}")
    people = [a for a in pull.approvals if a.kind == "human" and a.agent != pull.author]
    on_head = [a for a in people if a.commit is None or a.commit == pull.head]
    if on_head:
        return verdict(REVIEWED, f"{ref} approved by {', '.join(sorted(a.name for a in on_head))}")
    if people or heads:
        what = ", ".join(sorted(f"{a.name} at {(a.commit or '')[:7]}" for a in people)) or "a proposal"
        return verdict(STALE, f"{ref}: only an earlier commit was approved ({what})")
    by = f" by {pull.merger_name}" if pull.merger_name else ""
    machines = [a for a in pull.approvals if a.kind != "human"]
    note = f"; machine approvals do not count ({', '.join(a.name for a in machines)})" if machines else ""
    return verdict(UNREVIEWED, f"{ref} merged{by} with no approval by another person{note}")


# Reading the kernel -----------------------------------------------------------------------

CHANGES = """
MATCH (r:Entity {id: $repo})-[e:participates_in]->(c:Event {kind: 'change'})
WHERE e.belief_status = 'accepted'
RETURN DISTINCT c.id AS id, c.name AS name, c.props AS props, c.created_offset AS offset
"""

PULLS = """
MATCH (r:Entity {id: $repo})-[e:participates_in]->(p:Event {kind: 'pull_request'})
WHERE e.belief_status = 'accepted'
RETURN DISTINCT p.id AS id, p.name AS name, p.props AS props
"""

MERGES = """
MATCH (r:Entity {id: $repo})-[:participates_in]->(p:Event {kind: 'pull_request'}),
      (p)-[m:causes]->(c:Event {kind: 'change'})
WHERE m.belief_status = 'accepted'
RETURN p.id AS pull, c.id AS change, m.props AS props
"""

PEOPLE = """
MATCH (r:Entity {id: $repo})-[:participates_in]->(p:Event {kind: 'pull_request'}),
      (a:Agent)-[e:participates_in]->(p)
WHERE e.belief_status = 'accepted'
RETURN p.id AS pull, a.id AS agent, a.name AS name, e.props AS props
"""

APPROVALS = """
MATCH (r:Entity {id: $repo})-[:participates_in]->(p:Event {kind: 'pull_request'}),
      (p)-[e:approved_by]->(a:Agent)
WHERE e.belief_status = 'accepted'
RETURN p.id AS pull, a.id AS agent, a.kind AS kind, a.name AS name, e.props AS props
"""

PROPOSALS = """
MATCH (c:Claim {kind: 'proposed'})
WHERE c.status = 'approved'
RETURN c.id AS id, c.props AS props
"""

DEPLOYMENTS = """
MATCH (i:Entity {kind: 'image'})-[b:depends_on]->(r:Entity {id: $repo}),
      (i)-[e:participates_in]->(d:Event {kind: 'deployment'})
WHERE b.kind = 'built_from' AND b.belief_status = 'accepted' AND e.belief_status = 'accepted'
RETURN DISTINCT d.id AS id, d.name AS name
"""

REVISIONS = """
MATCH (c:Event {kind: 'change'})-[e:part_of]->(d:Event {kind: 'deployment'})
WHERE e.belief_status = 'accepted'
RETURN d.id AS deployment, c.id AS change, c.props AS props
"""


async def _call(client: Any, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(tool, args)
    body = json.loads(result.content[0].text)
    if result.is_error:
        raise AuditError(f"{tool}: {body.get('detail')}")
    return body


async def _rows(client: Any, cypher: str, repo: str) -> list[dict[str, Any]]:
    body = await _call(client, "query_graph", {"cypher": cypher, "params": {"repo": repo}, "limit": 1000})
    if body["truncated"]:
        raise AuditError("more than 1000 rows; the audit does not page yet")
    return list(body["rows"])


async def repository_id(client: Any, url: str) -> str:
    """The repository node the kernel holds for `url`, by its identity key."""
    query = {"name": repo_name(url), "type": "Entity", "kind": "repository", "identity": {"url": url}}
    body = await _call(client, "lookup_entities", {"queries": [query], "limit": 5})
    for candidate in body["results"][0]["candidates"]:
        if candidate["stage"] == "identity":
            return str(candidate["node_id"])
    raise AuditError(f"the kernel holds no repository {url}; map it first (wmk-software map)")


async def read(client: Any, url: str) -> Facts:
    """What the kernel holds about the repository at `url`."""
    repo = await repository_id(client, url)
    facts = Facts(url)
    for row in await _rows(client, CHANGES, repo):
        props = row.get("props") or {}
        facts.changes.append(Change(row["id"], row["name"], props.get("commit"), int(row.get("offset") or 0)))
    for row in await _rows(client, PULLS, repo):
        props = row.get("props") or {}
        number = props.get("number")
        facts.pulls[row["id"]] = Pull(
            row["id"], row["name"], int(number) if number is not None else None, props.get("url")
        )
    for row in await _rows(client, MERGES, repo):
        pull = facts.pulls.get(row["pull"])
        if pull is None:
            continue
        facts.merged[row["change"]] = pull.id
        pull.head = (row.get("props") or {}).get("head")
    for row in await _rows(client, PEOPLE, repo):
        pull = facts.pulls.get(row["pull"])
        role = (row.get("props") or {}).get("role")
        if pull is not None and role == "author":
            pull.author = row["agent"]
        elif pull is not None and role == "merger":
            pull.merger, pull.merger_name = row["agent"], row["name"]
    for row in await _rows(client, APPROVALS, repo):
        pull = facts.pulls.get(row["pull"])
        if pull is not None:
            commit = (row.get("props") or {}).get("commit")
            pull.approvals.append(Approval(row["agent"], row["kind"], row["name"], commit))
    for row in await _rows(client, PROPOSALS, repo):
        change = (row.get("props") or {}).get("change")
        if isinstance(change, dict):
            facts.proposals.append((row["id"], change))
    facts.deployments = [(row["id"], row["name"]) for row in await _rows(client, DEPLOYMENTS, repo)]
    for row in await _rows(client, REVISIONS, repo):
        facts.revisions[row["deployment"]] = (row["change"], (row.get("props") or {}).get("commit"))
    return facts


async def audit(client: Any, url: str) -> Audit:
    return judge(await read(client, url))
