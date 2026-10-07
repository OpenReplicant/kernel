"""Intake from an engagement folder (ADR 0036).
- Northwind's folder through the gateway: the SOP, the people with their consent, each
  interviewee's turns sealed under their key, the export discovered. Conformance waits for
  the mapping, and the work left lists every source no claim cites yet.
- The scripted mapping (northwind-views) then meets what intake created; a second run checks
  conformance, and a third writes nothing.
- The report is the same as when the views are mapped before the log, but for its offsets.
- Transcripts split into turns by speaker label; an unknown label is refused. Engagement
  files are refused without consent, with a speaker who is not a person, or with a document
  not yet converted to text."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from psycopg import sql
from test_process_views import LUCIA, MAYA, assemble

from evals.player import Player, fixtures
from kernel import admin
from kernel.testing import KernelDB, gateway_client
from wmk_adapter.plan import Source
from wmk_process import config, intake, report
from wmk_process.intake import EngagementError, Turn

pytestmark = pytest.mark.anyio

PACK = Path(__file__).resolve().parent.parent
ENGAGEMENT = PACK / "engagements" / "northwind" / "engagement.yaml"
VIEWS = PACK / "evals" / "fixtures" / "northwind-views"
TURNS = ["maya-1", "maya-2", "maya-3", "maya-4", "lucia-1", "lucia-2", "lucia-3"]


@pytest.fixture
def other_db(template_db: str) -> Iterator[str]:
    """A second fresh database, for mapping Northwind in the other order."""
    name = f"wmk_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin.admin_dsn(), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(template_db)
            )
        )
    yield name
    admin.drop_database(name)


def masked(text: str) -> str:
    return re.sub(r"log offset \d+", "log offset N", text)


async def test_intake_comes_before_the_mapping(gateway: Any, kdb: KernelDB, other_db: str) -> None:
    eng = intake.load(ENGAGEMENT)
    first = await intake.run(gateway, eng)
    assert first.failures == []
    # The SOP and seven turns; Maya and Lucia created, each by the claim of their consent.
    assert (first.sources.sources, first.sources.unchanged, first.sources.created) == (8, 0, 2)
    rows = kdb.q(
        "SELECT s.collection, count(*), bool_and(s.subjects = ARRAY[s.author_agent_id]), "
        "bool_and(starts_with(s.content, 'wmk:sealed:')) FROM kernel.sources s "
        "WHERE starts_with(s.collection, 'interview:') GROUP BY 1 ORDER BY 1"
    )
    assert rows == [(MAYA, 4, True, True), (LUCIA, 3, True, True)]
    sop = kdb.q(
        "SELECT subjects, starts_with(content, 'wmk:sealed:') FROM kernel.sources "
        "WHERE collection = 'sop:fin-007'"
    )
    assert sop == [([], False)]
    consent = [
        t for (t,) in kdb.q("SELECT text FROM kernel.claims_view WHERE text LIKE '%%agreed on%%' ORDER BY 1")
    ]
    assert consent == [
        "Lucia Ferreira (lucia.ferreira@northwind.test) agreed on 2026-09-16 to be interviewed and recorded "
        f"({LUCIA}).",
        f"Maya Chen (maya.chen@northwind.test) agreed on 2026-09-15 to be interviewed and recorded ({MAYA}).",
    ]
    # Nothing is mapped yet, so conformance waits; the log's own view is written.
    assert first.unmapped == {
        "sop:fin-007": ["sop:fin-007"],
        MAYA: ["turn:2", "turn:4", "turn:6", "turn:8"],
        LUCIA: ["turn:2", "turn:4", "turn:6"],
    }
    (export,) = first.exports
    assert export.conformed is None and export.waiting == "waits until the 8 sources above are mapped"
    assert export.discovered.failures == [] and export.discovered.created > 0
    assert first.unviewed == []
    text = first.text()
    assert "Waiting to be mapped (8):\n- sop:fin-007: sop:fin-007\n" in text
    for word in ("Maya", "Lucia", "@northwind"):
        assert word not in text
    # The work file: every waiting chunk's id and words, each turn with its speaker.
    work = first.work()
    assert work.count("\n## ") == 8 and f"Read at log offset {first.offset}." in work
    assert [w.author is not None for w in first.waiting] == [False] + [True] * 7
    for w in first.waiting:
        assert [words for _, words in w.chunks] != [] and all(f"Chunk {c}:" in work for c, _ in w.chunks)
    maya = first.waiting[1]
    assert maya.author == kdb.one(
        "SELECT id FROM kernel.nodes WHERE identity ->> 'email' = 'maya.chen@northwind.test'"
    )
    assert f"## {MAYA} turn:2 ({MAYA}, turn 2): source {maya.source_id}, said by {maya.author}" in work
    assert "> I lead procurement at Northwind Foods, so the purchase orders are my team's job." in work
    headings = [line for line in work.splitlines() if line.startswith("#")]
    assert len(headings) == 9 and not any("Maya" in h or "Lucia" in h for h in headings)

    # The mapping: the scripted session reads the sources intake stored and meets its nodes.
    player = Player(gateway, fixtures(["northwind-views"])[0])
    await player.run()
    assert player.failures == [] and player.rejections == 0
    assert sorted(player.skipped_sources) == sorted(["sop", *TURNS])
    people = kdb.q("SELECT count(*) FROM kernel.nodes WHERE identity ->> 'email' LIKE '%%@northwind.test'")
    assert people == [(2,)]

    second = await intake.run(gateway, eng)
    assert second.failures == [] and second.unmapped == {}
    assert (second.sources.unchanged, second.sources.claims) == (8, 0)
    (export,) = second.exports
    assert export.discovered.unchanged == 1 and export.conformed is not None
    assert export.conformed.denied == 3
    assert "Nothing is waiting to be mapped." in second.text()

    head = kdb.head()
    third = await intake.run(gateway, eng)
    assert kdb.head() == head and third.exports[0].conformed is not None
    assert third.exports[0].conformed.unchanged == 1

    # The same report as with the views mapped first (make northwind's old order).
    cfg = config.load(eng.done[0])
    ours = (await report.read(gateway, cfg)).text()
    async with gateway_client(other_db) as other:
        theirs = (await report.read(other, await assemble(other))).text()
    assert masked(ours) == masked(theirs)


def test_northwinds_transcripts_hold_the_fixtures_turns() -> None:
    plan = intake.plan(intake.load(ENGAGEMENT))
    turns = [s for s in plan.sources if s.author]
    assert [s.content for s in turns] == [(VIEWS / "sources" / f"{n}.txt").read_text() for n in TURNS]
    assert [s.uri for s in turns] == ["turn:2", "turn:4", "turn:6", "turn:8", "turn:2", "turn:4", "turn:6"]
    # Names appear only in what is sealed: the content and the people's nodes.
    for s in plan.sources:
        for field in (s.alias, s.uri, s.collection, s.title):
            assert "Maya" not in field and "Lucia" not in field
    with pytest.raises(ValueError, match="its author must be one of its subjects"):
        Source("x", "text", "x", "turn:1", "c", author="person:a")


def test_turns_follow_the_speaker_labels() -> None:
    text = (
        "Interview 7, 1 October 2026\n"
        "\n"
        "Interviewer: Who approves?\n"
        "Ana Ruiz: The controller.\n"
        "Usually within a day.\n"
        "\n"
        "Interviewer:\n"
        "Ana Ruiz: In practice: whoever is in.\n"
    )
    assert intake.turns(text, ["Interviewer", "Ana Ruiz"]) == [
        Turn(1, "Interviewer", "Who approves?\n"),
        Turn(2, "Ana Ruiz", "The controller.\nUsually within a day.\n"),
        Turn(4, "Ana Ruiz", "In practice: whoever is in.\n"),
    ]
    with pytest.raises(EngagementError, match="line 2: unknown speaker 'Ana Ruis'"):
        intake.turns("Ana Ruiz: Yes.\nAna Ruis: No.\n", ["Ana Ruiz"])


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"consent": None}, "needs consent"),
        ({"consent": "last week"}, "needs consent"),
        (
            {"speakers": {"Ana Ruiz": "bea", "Interviewer": None}},
            "speaker 'Ana Ruiz' is 'bea', who is not in people",
        ),
        ({"speakers": {"Ana Ruiz": None, "Interviewer": None}}, "records no one"),
    ],
)
def test_a_session_needs_consent_and_its_people(tmp_path: Path, change: dict[str, Any], error: str) -> None:
    (tmp_path / "t.txt").write_text("Ana Ruiz: Yes.\n")
    session = {
        "file": "t.txt",
        "collection": "interview:7",
        "consent": "2026-10-01",
        "speakers": {"Ana Ruiz": "ana", "Interviewer": None},
    } | change
    path = engagement(tmp_path, {"told": [session]})
    with pytest.raises(EngagementError, match=re.escape(error)):
        intake.load(path)


def test_documents_are_converted_first(tmp_path: Path) -> None:
    (tmp_path / "sop.pdf").write_bytes(b"%PDF-1.7")
    with pytest.raises(EngagementError, match="convert it first"):
        intake.load(engagement(tmp_path, {"written": [{"file": "sop.pdf", "collection": "sop:1"}]}))
    (tmp_path / "sop.md").write_text("# SOP\n\nThe controller approves.\n")
    doc = {"file": "sop.md", "collection": "sop:1", "converted_from": "sop.pdf"}
    (source,) = intake.plan(intake.load(engagement(tmp_path, {"written": [doc]}))).sources
    assert source.uri == "sop.md" and source.media_type == "text/markdown"
    assert (
        source.metadata["converted_from"] == "sop.pdf" and len(source.metadata["converted_from_sha256"]) == 64
    )


def engagement(folder: Path, parts: dict[str, Any]) -> Path:
    path = folder / "engagement.yaml"
    path.write_text(
        yaml.safe_dump(
            {"name": "Test", "people": {"ana": {"name": "Ana Ruiz", "email": "ana@x.test"}}} | parts
        )
    )
    return path
