"""The forge as evidence (ADR 0031): a capture keeps an allowlist and never free text, each
pull request is a sealed source naming its people, approvals are evidence on the pull
request and never a decision, a dismissed approval is retracted, and the audit tells
decided, reviewed, stale, unreviewed and unknown changes apart, with the deployments of
changes nobody approved."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from kernel.testing import KernelDB
from wmk_software import audit, forge, observe
from wmk_software.apply import apply
from wmk_software.docker import Capture as DockerCapture
from wmk_software.docker import Container
from wmk_software.forge import Account, Pull, Review
from wmk_software.mapping import build
from wmk_software.repo import Repository

pytestmark = pytest.mark.anyio

URL = "https://github.com/example/toy"
DATA = Path(__file__).resolve().parent / "data"
ALICE = Account("alice", "https://github.com/alice")
BOB = Account("bob", "https://github.com/bob")
BOT = Account("ci-bot[bot]", "https://github.com/apps/ci-bot", bot=True)
COMPOSE = "name: toy\nservices:\n  app:\n    build: .\n"
# First-parent history: a pushed directly, then one merge per pull request #1 to #5.
COMMITS = ["a", "b", "c", "d", "e", "f"]


def at(day: int, hour: int = 12) -> str:
    return f"2026-10-0{day}T{hour:02d}:00:00Z"


def pull(number: int, merge: str | None, reviews: tuple[Review, ...] = (), **over: Any) -> Pull:
    head = str(number) * 40
    spec: dict[str, Any] = {
        "number": number,
        "url": f"{URL}/pull/{number}",
        "title": f"Change {number + 1}",
        "author": ALICE,
        "state": "merged" if merge else "open",
        "base": "main",
        "head": head,
        "created_at": at(number, 9),
        "merged_at": at(number) if merge else None,
        "closed_at": at(number) if merge else None,
        "merge_commit": merge * 40 if merge else None,
        "merged_by": ALICE if merge else None,
        "reviews": reviews,
    }
    return Pull(**{**spec, **over})


def capture(*pulls: Pull) -> forge.Capture:
    return forge.Capture("github", URL, "main", at(9), True, list(pulls))


def scenario(first_approval: str = "approved", sixth: str | None = None) -> forge.Capture:
    return capture(
        # Reviewed: bob approved the merged head.
        pull(1, "b", (Review(BOB, first_approval, "1" * 40, at(1, 11)),)),
        # Unreviewed: merged by its author with no review.
        pull(2, "c"),
        # Stale: bob approved an earlier commit only.
        pull(3, "d", (Review(BOB, "approved", "9" * 40, at(3, 10)),)),
        # Decided: no review, but an approved proposal names it (below).
        pull(4, "e"),
        # Unreviewed: only a bot approved.
        pull(5, "f", (Review(BOT, "approved", "5" * 40, at(5, 11)),)),
        # Open, approved by bob: no change yet.
        pull(6, sixth, (Review(BOB, "approved", "6" * 40, at(6, 11)),)),
    )


# Capture ----------------------------------------------------------------------------------


def github(path: str) -> Any:
    """GitHub's REST API for a small repository, with what must never be kept."""
    user = {"login": "alice", "html_url": "https://github.com/alice", "type": "User", "email": "a@x.test"}
    bot = {"login": "ci-bot[bot]", "html_url": "https://github.com/apps/ci-bot", "type": "Bot"}
    bob = {"login": "bob", "html_url": "https://github.com/bob", "type": "User"}
    if path == "/repos/example/toy":
        return {"html_url": URL, "default_branch": "main", "private": False}
    if path.startswith("/repos/example/toy/pulls?"):
        if "page=1" not in path:
            return []
        return [
            {
                "number": 2,
                "html_url": f"{URL}/pull/2",
                "title": "Open work",
                "body": "Secret plans in the description.",
                "user": user,
                "state": "open",
                "base": {"ref": "main"},
                "head": {"sha": "2" * 40, "ref": "feature"},
                "created_at": at(2),
                "closed_at": None,
                "merged_at": None,
                "merge_commit_sha": "7" * 40,  # GitHub's test merge: not a merge
            },
            {
                "number": 1,
                "html_url": f"{URL}/pull/1",
                "title": "First change",
                "body": "More free text.",
                "user": user,
                "state": "closed",
                "base": {"ref": "main"},
                "head": {"sha": "1" * 40},
                "created_at": at(1, 9),
                "closed_at": at(1),
                "merged_at": at(1),
                "merge_commit_sha": "b" * 40,
            },
        ]
    if path == "/repos/example/toy/pulls/1":
        return {"merged_by": user}
    if path.startswith("/repos/example/toy/pulls/1/reviews"):
        return [
            {
                "user": bob,
                "state": "COMMENTED",
                "commit_id": "1" * 40,
                "submitted_at": at(1, 10),
                "body": "nit",
            },
            {
                "user": bob,
                "state": "APPROVED",
                "commit_id": "1" * 40,
                "submitted_at": at(1, 11),
                "body": "ok",
            },
            {"user": bot, "state": "APPROVED", "commit_id": "1" * 40, "submitted_at": at(1, 11)},
            {"user": bob, "state": "PENDING", "commit_id": "1" * 40},
        ]
    if path.startswith("/repos/example/toy/pulls/2/reviews"):
        return []
    raise AssertionError(f"unexpected GET {path}")


def test_a_capture_keeps_an_allowlist_and_no_free_text(tmp_path: Path) -> None:
    cap = forge.capture("example/toy", fetch=github, limit=10)
    assert (cap.repository, cap.default_branch, cap.complete) == (URL, "main", True)
    first, second = cap.pulls
    assert (first.number, first.state, first.merge_commit, first.merged_by) == (1, "merged", "b" * 40, ALICE)
    assert [(r.reviewer.login, r.state) for r in first.reviews] == [
        ("bob", "commented"),
        ("bob", "approved"),
        ("ci-bot[bot]", "approved"),
    ]
    assert first.reviews[2].reviewer.bot
    # An open pull request's test merge commit is not a merge.
    assert (second.state, second.merge_commit, second.merged_by) == ("open", None, None)
    text = cap.to_json()
    for never in ("Secret plans", "free text", "nit", "a@x.test", "feature"):
        assert never not in text
    (tmp_path / "forge.json").write_text(text)
    assert forge.load(tmp_path / "forge.json") == cap
    (tmp_path / "other.json").write_text(json.dumps({"format": "x"}))
    with pytest.raises(forge.ForgeError, match="not a wmk-forge-capture"):
        forge.load(tmp_path / "other.json")
    # A limit below the number of pull requests makes the capture partial.
    assert forge.capture("example/toy", fetch=github, limit=1).complete is False
    with pytest.raises(forge.ForgeError, match="owner/name"):
        forge.capture("toy", fetch=github)


def test_each_reviewers_latest_decisive_review_stands() -> None:
    def approvers(*reviews: Review, **over: Any) -> list[str]:
        return [r.reviewer.login for r in pull(1, "b", reviews, **over).approvals()]

    head = "1" * 40
    assert approvers(Review(BOB, "approved", head, at(1, 10))) == ["bob"]
    assert (
        approvers(Review(BOB, "approved", head, at(1, 10)), Review(BOB, "changes_requested", head, at(1, 11)))
        == []
    )
    assert approvers(
        Review(BOB, "changes_requested", head, at(1, 10)), Review(BOB, "approved", head, at(1, 11))
    ) == ["bob"]
    assert approvers(Review(BOB, "approved", head, at(1, 10)), Review(BOB, "commented", head, at(1, 11))) == [
        "bob"
    ]
    assert approvers(Review(BOB, "dismissed", head, at(1, 10))) == []
    # A review after the merge does not change what the merge rested on.
    assert approvers(Review(BOB, "approved", head, at(1, 13))) == []
    assert approvers(Review(BOB, "approved", head, at(1, 10)), Review(BOB, "dismissed", head, at(1, 13))) == [
        "bob"
    ]


def test_each_pull_request_is_a_source_naming_its_people() -> None:
    plan = forge.build(scenario())
    sources = {s.alias: s for s in plan.sources}
    first = sources["pull:1"]
    assert (first.collection, first.uri, first.origins) == (
        f"{URL}/pull/1",
        f"{URL}/pull/1",
        (f"forge:{URL}",),
    )
    assert first.subjects == (f"account:{ALICE.url}", f"account:{BOB.url}")
    # Bots are machines, not data subjects.
    assert sources["pull:5"].subjects == (f"account:{ALICE.url}",)
    assert plan.nodes[f"account:{BOT.url}"]["kind"] == "machine"
    assert plan.nodes[f"account:{ALICE.url}"] == {
        "type": "Agent",
        "kind": "human",
        "name": "alice",
        "identity": {"account": ALICE.url},
    }
    assert "Merged: 2026-10-01T12:00:00Z by alice (https://github.com/alice) as " + "b" * 40 in first.content
    texts = [f.text for f in plan.facts if f.source == "pull:1"]
    assert texts == [
        "alice opened pull request #1 of example/toy on 2026-10-01 against main: Change 2.",
        "bob approved pull request #1 of example/toy on 2026-10-01, at commit 1111111.",
        "alice merged pull request #1 of example/toy into main on 2026-10-01 as commit bbbbbbb.",
    ]
    # Every claim's offset points at the line it rests on.
    for fact in plan.facts:
        line = sources[fact.source].content[fact.at :].split("\n")[0]
        assert line.startswith(("Opened:", "- ", "Merged:")), line
    with pytest.raises(ValueError, match="subjects"):
        plan.script()


def toy(compose: str, commits: list[str]) -> Repository:
    """A repository whose first-parent history is one commit per letter, oldest first."""
    lines = [f"{c * 40} 2026-10-0{i + 1}T12:00:00Z Change {i + 1}" for i, c in enumerate(commits)]
    history = f"Repository: {URL}\nBranch: main\n\nFirst-parent commits, newest first:\n\n"
    history += "\n".join(reversed(lines)) + "\n"
    return Repository(URL, "example/toy", commits[-1] * 40, "main", {"compose.yaml": compose}, history)


async def test_the_forge_is_evidence_and_the_audit_judges_from_it(
    gateway: Any, kdb: KernelDB, agent: str
) -> None:
    assert (await apply(gateway, build(toy(COMPOSE, COMMITS)))).failures == []
    first = await apply(gateway, forge.build(scenario()))
    assert first.failures == [] and first.sources == 6

    # The people are agents keyed by their account; humans are sealed, and each record is
    # sealed under its people's keys.
    people = dict(
        kdb.q(
            "SELECT identity ->> 'account', kind FROM kernel.nodes "
            "WHERE type = 'Agent' AND identity ? 'account'"
        )
    )
    assert people == {ALICE.url: "human", BOB.url: "human", BOT.url: "machine"}
    alice, bob = (
        kdb.one("SELECT id FROM kernel.nodes WHERE identity ->> 'account' = %s", [a.url])
        for a in (ALICE, BOB)
    )
    subjects, sealed = kdb.q(
        "SELECT s.subjects, v.sealed FROM kernel.sources s JOIN kernel.sources_view v ON v.id = s.id "
        "WHERE s.collection = %s",
        [f"{URL}/pull/1"],
    )[0]
    assert sorted(subjects) == sorted([alice, bob]) and sealed
    # Approvals are edges on the pull request, never on a proposal; open is ongoing.
    approvals = kdb.q(
        "SELECT p.name, a.name, e.props ->> 'commit', e.belief_status FROM kernel.edges e "
        "JOIN kernel.nodes p ON p.id = e.from_id JOIN kernel.nodes a ON a.id = e.to_id "
        "WHERE e.edge = 'approved_by' ORDER BY p.name, a.name"
    )
    assert approvals == [
        ("#1 Change 2", "bob", "1" * 40, "accepted"),
        ("#3 Change 4", "bob", "9" * 40, "accepted"),
        ("#5 Change 6", "ci-bot[bot]", "5" * 40, "accepted"),
        ("#6 Change 7", "bob", "6" * 40, "accepted"),
    ]
    statuses = dict(kdb.q("SELECT name, status FROM kernel.nodes WHERE kind = 'pull_request'"))
    assert statuses["#1 Change 2"] == "completed" and statuses["#6 Change 7"] == "ongoing"
    # The merge commit is the same change the git history wrote.
    assert kdb.one("SELECT count(*) FROM kernel.nodes WHERE kind = 'change'") == len(COMMITS)

    # A person approves a proposal naming #4, in the explorer.
    repo = kdb.one("SELECT id FROM kernel.nodes WHERE kind = 'repository'")
    proposal = kdb.claim(
        agent,
        "Merge pull request #4.",
        [
            {
                "op": "promote",
                "ref": "$p",
                "about": [repo],
                "props": {"change": {"kind": "pull_request", "url": f"{URL}/pull/4"}},
            }
        ],
        basis="inferred",
        modality="proposed",
    )["refs"]["$p"]
    kdb.decide("dana@example.test", {"action": "approve", "proposal": proposal})

    # What runs: the app's container was built from commit c, which nobody approved.
    docker = DockerCapture(
        "dev-host",
        "toy",
        at(8),
        [
            Container(
                "c" * 64, "toy-app-1", "app", "toy-app:dev", "sha256:1", at(7), "running", revision="c" * 40
            ),
            Container("d" * 64, "toy-old-1", "app", "toy-app:old", "sha256:2", at(7), "exited"),
        ],
    )
    assert (await apply(gateway, observe.build(docker, repo=URL))).failures == []

    result = await audit.audit(gateway, URL)
    by_commit = {v.commit: (v.status, v.pull) for v in result.changes}
    assert by_commit == {
        "f" * 40: (audit.UNREVIEWED, 5),
        "e" * 40: (audit.DECIDED, 4),
        "d" * 40: (audit.STALE, 3),
        "c" * 40: (audit.UNREVIEWED, 2),
        "b" * 40: (audit.REVIEWED, 1),
        "a" * 40: (audit.NO_PULL, None),
    }
    reasons = {v.pull: v.reason for v in result.changes}
    assert "machine approvals do not count (ci-bot[bot])" in reasons[5]
    assert reasons[1] == "#1 approved by bob"
    assert [(d.status, d.commit) for d in result.deployments] == [
        (audit.UNREVIEWED, "c" * 40),
        (audit.NO_REVISION, None),
    ]
    assert len(result.deployed_without_approval()) == 1
    assert (
        "Changes to example/toy: 6 (1 decided, 1 reviewed, 1 stale, 2 unreviewed, 1 no pull request known)"
        in (result.text())
    )

    # Capturing again: an unchanged pull request writes nothing; bob's approval of #1 was
    # dismissed, so its run retracts the approval and the change is no longer reviewed; #6
    # was merged, so it is completed.
    again = await apply(gateway, forge.build(scenario(first_approval="dismissed", sixth="7")))
    assert again.failures == [] and again.unchanged == 4 and again.retracted == 1
    assert kdb.one("SELECT status FROM kernel.nodes WHERE name = '#6 Change 7'") == "completed"
    edge = kdb.one(
        "SELECT e.belief_status FROM kernel.edges e JOIN kernel.nodes p ON p.id = e.from_id "
        "WHERE e.edge = 'approved_by' AND p.name = '#1 Change 2'"
    )
    assert edge != "accepted"
    after = {v.commit: v.status for v in (await audit.audit(gateway, URL)).changes}
    assert after["b" * 40] == audit.UNREVIEWED
    # The proposal's status is still the person's decision alone.
    assert kdb.one("SELECT status FROM kernel.nodes WHERE id = %s", [proposal]) == "approved"


async def test_the_audit_needs_the_repository_mapped(gateway: Any) -> None:
    with pytest.raises(audit.AuditError, match="map it first"):
        await audit.audit(gateway, "https://github.com/example/nowhere")


def test_deployments_of_changes_outside_the_history_are_not_approved() -> None:
    facts = audit.Facts(URL)
    facts.changes = [audit.Change("chg_1", "Change 1", "a" * 40, 1)]
    facts.deployments = [("dep_1", "toy/app at 1"), ("dep_2", "toy/app at 2")]
    facts.revisions = {"dep_2": ("chg_x", "e" * 40)}
    result = audit.judge(facts)
    assert [(d.deployment, d.status) for d in result.deployments] == [
        ("dep_1", audit.NO_REVISION),
        ("dep_2", audit.UNMERGED),
    ]
    assert [d.deployment for d in result.deployed_without_approval()] == ["dep_2"]
    assert json.loads(result.to_json())["counts"][audit.NO_PULL] == 1


def test_the_recorded_capture_of_this_repository() -> None:
    cap = forge.load(DATA / "github-kernel.json")
    assert cap.repository == "https://github.com/OpenReplicant/kernel" and cap.complete
    plan = forge.build(cap)
    assert len(plan.sources) == len(cap.pulls) and all(s.subjects for s in plan.sources)
    # Every merged pull request causes its merge commit.
    merged = [p for p in cap.pulls if p.merge_commit]
    causes = [op for f in plan.facts for op in f.ops if op.get("edge") == "causes"]
    assert len(causes) == len(merged)
    assert dataclasses.asdict(cap)["pulls"][0]["author"]["url"].startswith("https://github.com/")
