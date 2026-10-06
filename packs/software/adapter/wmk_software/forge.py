"""The forge as evidence (ADR 0031): pull requests, reviews and merges, captured from the
forge and written as observed claims, each citing its pull request's own record.

A capture reads GitHub's REST API, read-only, and keeps an allowlist: per pull request its
number, URL, title, author, state, base branch, head commit, times, merge commit and
merger; per review its reviewer, state, commit and time; per account its login, profile
URL and whether it is a bot. Descriptions, comments and review bodies are free text and are
never read. A capture is a JSON document; tests replay recorded ones, so only `capture`
needs the network.

Each pull request is a source of its own, collection its URL. Its people (author,
reviewers, merger) are the source's subjects, so their data is sealed. A pull request whose
record has not changed is skipped; a changed one is mapped in a run, so a dismissed
approval is retracted. Approvals are `approved_by` edges on the pull request: evidence of
a decision on the forge, never a decision on a kernel proposal.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wmk_software.plan import Plan, Source
from wmk_software.repo import repo_name

FORMAT = "wmk-forge-capture"
VERSION = 1
GITHUB_API = "https://api.github.com"
NS = "software"
# A reviewer's latest decisive review is what stands; comments and pending reviews decide nothing.
DECISIVE = ("approved", "changes_requested", "dismissed")
REVIEW_STATES = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "changes_requested",
    "COMMENTED": "commented",
    "DISMISSED": "dismissed",
}
STATUS = {"open": "ongoing", "merged": "completed", "closed": "cancelled"}


class ForgeError(Exception):
    """The forge could not be read, or a capture file is not one."""


@dataclass(frozen=True)
class Account:
    login: str
    url: str  # the profile URL: the account's identity key
    bot: bool = False


@dataclass(frozen=True)
class Review:
    reviewer: Account
    state: str  # approved, changes_requested, commented, dismissed
    commit: str
    submitted_at: str


@dataclass(frozen=True)
class Pull:
    number: int
    url: str
    title: str
    author: Account
    state: str  # open, merged, closed
    base: str
    head: str
    created_at: str
    closed_at: str | None = None
    merged_at: str | None = None
    merge_commit: str | None = None
    merged_by: Account | None = None
    reviews: tuple[Review, ...] = ()

    def approvals(self) -> list[Review]:
        """Each reviewer whose latest decisive review approves: judged when the pull request
        was merged or closed, else now. In review order."""
        cutoff = self.merged_at or self.closed_at
        latest: dict[str, Review] = {}
        for review in sorted(self.reviews, key=lambda r: r.submitted_at):
            if cutoff and review.submitted_at > cutoff:
                continue
            if review.state in DECISIVE:
                latest.pop(review.reviewer.url, None)
                latest[review.reviewer.url] = review
        return [r for r in latest.values() if r.state == "approved"]

    def people(self) -> list[Account]:
        """Everyone the record names, in order of first appearance."""
        seen: dict[str, Account] = {}
        for account in [self.author, *(r.reviewer for r in self.reviews), self.merged_by]:
            if account is not None:
                seen.setdefault(account.url, account)
        return list(seen.values())


@dataclass
class Capture:
    forge: str
    repository: str  # the repository's URL
    default_branch: str
    observed_at: str
    # Whether every pull request is in the capture, or only the newest up to the limit.
    complete: bool
    pulls: list[Pull] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({"format": FORMAT, "version": VERSION, **asdict(self)}, indent=2) + "\n"


def _account(data: dict[str, Any] | None) -> Account | None:
    if not data:
        return None
    return Account(str(data["login"]), str(data["url"]), bool(data.get("bot", False)))


def load(path: Path | str) -> Capture:
    """A capture written by `capture`."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise ForgeError(f"{path}: {exc}") from None
    if data.get("format") != FORMAT or data.get("version") != VERSION:
        raise ForgeError(f"{path} is not a {FORMAT} version {VERSION} file")
    pulls = []
    for p in data["pulls"]:
        author = _account(p["author"])
        if author is None:
            raise ForgeError(f"{path}: pull request #{p['number']} names no author")
        reviews = []
        for r in p.get("reviews", []):
            reviewer = _account(r["reviewer"])
            if reviewer is None:
                raise ForgeError(f"{path}: a review of #{p['number']} names no reviewer")
            reviews.append(Review(reviewer, r["state"], r["commit"], r["submitted_at"]))
        pulls.append(
            Pull(
                number=int(p["number"]),
                url=p["url"],
                title=p["title"],
                author=author,
                state=p["state"],
                base=p["base"],
                head=p["head"],
                created_at=p["created_at"],
                closed_at=p.get("closed_at"),
                merged_at=p.get("merged_at"),
                merge_commit=p.get("merge_commit"),
                merged_by=_account(p.get("merged_by")),
                reviews=tuple(reviews),
            )
        )
    return Capture(
        forge=data["forge"],
        repository=data["repository"],
        default_branch=data["default_branch"],
        observed_at=data["observed_at"],
        complete=bool(data["complete"]),
        pulls=pulls,
    )


# Reading GitHub ---------------------------------------------------------------------------

Fetch = Callable[[str], Any]


def github_fetch(api: str = GITHUB_API, token: str | None = None) -> Fetch:
    """GET a path of GitHub's REST API and decode its JSON. Read-only."""

    def fetch(path: str) -> Any:
        request = urllib.request.Request(
            f"{api.rstrip('/')}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "wmk-software",
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise ForgeError(f"GET {path}: {exc.code} {exc.reason}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise ForgeError(f"GET {path}: {exc}") from None

    return fetch


def _github_account(user: dict[str, Any] | None) -> Account | None:
    if not user:
        return None
    return Account(str(user["login"]), str(user["html_url"]), user.get("type") == "Bot")


def capture(
    repository: str,
    *,
    limit: int = 50,
    fetch: Fetch | None = None,
    now: datetime | None = None,
) -> Capture:
    """The newest `limit` pull requests of `repository` (owner/name) on GitHub, with their
    reviews and mergers. GITHUB_TOKEN, when set, is sent; it needs only read access."""
    if repository.count("/") != 1:
        raise ForgeError(f"{repository!r}: name the repository as owner/name")
    fetch = fetch or github_fetch(token=os.environ.get("GITHUB_TOKEN") or None)
    base = f"/repos/{urllib.parse.quote(repository)}"
    repo = fetch(base)
    listed: list[dict[str, Any]] = []
    page = 1
    complete = False
    while len(listed) < limit:
        batch = fetch(f"{base}/pulls?state=all&sort=created&direction=desc&per_page=100&page={page}")
        listed += batch
        if len(batch) < 100:
            complete = len(listed) <= limit
            break
        page += 1
    listed = listed[:limit]
    pulls = []
    for p in listed:
        number = int(p["number"])
        merged_by = None
        if p.get("merged_at"):
            merged_by = _github_account(fetch(f"{base}/pulls/{number}").get("merged_by"))
        reviews = []
        for r in fetch(f"{base}/pulls/{number}/reviews?per_page=100"):
            state = REVIEW_STATES.get(str(r.get("state")))
            reviewer = _github_account(r.get("user"))
            if state and reviewer and r.get("submitted_at"):
                reviews.append(Review(reviewer, state, str(r.get("commit_id") or ""), str(r["submitted_at"])))
        author = _github_account(p.get("user"))
        if author is None:
            continue
        pulls.append(
            Pull(
                number=number,
                url=str(p["html_url"]),
                title=str(p["title"]),
                author=author,
                state="merged" if p.get("merged_at") else str(p["state"]),
                base=str(p["base"]["ref"]),
                head=str(p["head"]["sha"]),
                created_at=str(p["created_at"]),
                closed_at=p.get("closed_at"),
                merged_at=p.get("merged_at"),
                merge_commit=p.get("merge_commit_sha") if p.get("merged_at") else None,
                merged_by=merged_by,
                reviews=tuple(reviews),
            )
        )
    observed = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Capture(
        forge="github",
        repository=str(repo["html_url"]),
        default_branch=str(repo["default_branch"]),
        observed_at=observed,
        complete=complete,
        pulls=sorted(pulls, key=lambda p: p.number),
    )


# Mapping ----------------------------------------------------------------------------------


def who(account: Account) -> str:
    return f"{account.login} ({account.url})"


def record(pull: Pull, repository: str) -> tuple[str, dict[str, int]]:
    """A pull request's record as text, and where each part of it starts: `opened`, `merged`,
    `closed` and `review:<n>`. Stable while the pull request does not change."""
    lines = [
        f"Pull request #{pull.number} of {repo_name(repository)}: {pull.title}",
        f"URL: {pull.url}",
        f"Repository: {repository}",
    ]
    at: dict[str, int] = {}

    def add(key: str | None, line: str) -> None:
        if key:
            at[key] = sum(len(x) + 1 for x in lines)
        lines.append(line)

    add("opened", f"Opened: {pull.created_at} by {who(pull.author)} against {pull.base}")
    add(None, f"Head: {pull.head}")
    add(None, f"State: {pull.state}")
    if pull.merged_at and pull.merge_commit:
        by = f" by {who(pull.merged_by)}" if pull.merged_by else ""
        add("merged", f"Merged: {pull.merged_at}{by} as {pull.merge_commit}")
    elif pull.closed_at:
        add("closed", f"Closed: {pull.closed_at} without merging")
    add(None, "")
    add(None, "Reviews:" if pull.reviews else "Reviews: none")
    for i, r in enumerate(sorted(pull.reviews, key=lambda r: r.submitted_at)):
        commit = f" at {r.commit}" if r.commit else ""
        add(f"review:{i}", f"- {r.submitted_at}: {who(r.reviewer)} {r.state.replace('_', ' ')}{commit}")
    return "\n".join(lines) + "\n", at


def day(stamp: str) -> str:
    return stamp[:10]


def build(cap: Capture) -> Plan:
    """The plan for a capture: per pull request, its record as a source and what it shows."""
    plan = Plan()
    repo = plan.node(
        "repo",
        type="Entity",
        kind="repository",
        namespace=NS,
        name=repo_name(cap.repository),
        identity={"url": cap.repository},
    )

    def agent(account: Account) -> str:
        return plan.node(
            f"account:{account.url}",
            type="Agent",
            kind="machine" if account.bot else "human",
            name=account.login,
            identity={"account": account.url},
        )

    for pull in cap.pulls:
        text, at = record(pull, cap.repository)
        reviews = sorted(pull.reviews, key=lambda r: r.submitted_at)
        src = plan.source(
            Source(
                alias=f"pull:{pull.number}",
                content=text,
                title=f"Pull request #{pull.number} of {repo_name(cap.repository)}",
                uri=pull.url,
                collection=pull.url,
                metadata={"repository": cap.repository, "number": pull.number, "forge": cap.forge},
                origins=(f"forge:{cap.repository}",),
                subjects=tuple(agent(a) for a in pull.people() if not a.bot),
            )
        )
        pr = plan.node(
            f"pull:{pull.url}",
            type="Event",
            kind="pull_request",
            namespace=NS,
            name=f"#{pull.number} {pull.title}",
            identity={"url": pull.url},
            start=pull.created_at,
            props={"number": pull.number, "url": pull.url},
        )
        n = f"pull request #{pull.number} of {repo_name(cap.repository)}"
        title = pull.title if pull.title.rstrip().endswith((".", "!", "?")) else f"{pull.title}."
        fact = plan.fact(
            f"{pull.author.login} opened {n} on {day(pull.created_at)} against {pull.base}: {title}",
            source=src.alias,
            at=at["opened"],
        )
        plan.edge(fact, "participates_in", agent(pull.author), pr, {"role": "author"})
        plan.edge(fact, "participates_in", repo, pr, {"role": "target"})
        fact.ops.append({"op": "transition", "node": pr, "status": STATUS[pull.state]})
        for review in pull.approvals():
            i = reviews.index(review)
            fact = plan.fact(
                f"{review.reviewer.login} approved {n} on {day(review.submitted_at)}"
                + (f", at commit {review.commit[:7]}." if review.commit else "."),
                source=src.alias,
                at=at[f"review:{i}"],
            )
            plan.edge(
                fact,
                "approved_by",
                pr,
                agent(review.reviewer),
                {"commit": review.commit} if review.commit else None,
                valid_from=review.submitted_at,
            )
        if pull.merged_at and pull.merge_commit:
            change = plan.node(
                f"commit:{pull.merge_commit[:12]}",
                type="Event",
                kind="change",
                namespace=NS,
                name=f"{pull.title} (#{pull.number})",
                identity={"commit": pull.merge_commit},
                start=pull.merged_at,
                props={"commit": pull.merge_commit, "pr": pull.number},
            )
            merged = f"into {pull.base} on {day(pull.merged_at)} as commit {pull.merge_commit[:7]}."
            fact = plan.fact(
                f"{pull.merged_by.login} merged {n} {merged}"
                if pull.merged_by
                else f"{n[0].upper()}{n[1:]} was merged {merged}",
                source=src.alias,
                at=at["merged"],
            )
            if pull.merged_by:
                plan.edge(fact, "participates_in", agent(pull.merged_by), pr, {"role": "merger"})
            plan.edge(fact, "causes", pr, change, {"head": pull.head})
    plan.drop_empty()
    return plan
