"""Intake from an engagement folder (ADR 0036): a client's documents, interview transcripts
and exports, written into the kernel through the gateway by one command.

One YAML file describes the folder; its paths are relative to it.

name: Northwind purchase requests
people:                             # the people the transcripts record, by a key of your own
  maya: {name: Maya Chen, email: maya.chen@northwind.test}
written:                            # documents, as written: Markdown or plain text
  - file: written/sop-fin-007.md
    collection: "sop:fin-007"       # stable across versions; never a person's name
    title: Purchase request procedure (SOP-FIN-007)
    uri: "sop:fin-007"              # where it came from (default: the file's path)
    origins: ["org:northwind-finance"]
    converted_from: written/sop-fin-007.pdf   # the original, when a converter made `file`
told:                               # interview transcripts, as told: one file per session
  - file: told/nw-2026-09-15-a.txt
    collection: "interview:nw-2026-09-15-a"
    consent: 2026-09-15             # when the people speaking agreed to be recorded
    speakers: {Maya Chen: maya, Interviewer: null}   # label -> person; null is not recorded
done:                               # exports, as done: wmk-process configurations
  - purchase-requests.yaml

An intake run, in order:
- ingests each document as a source;
- creates each person a transcript records, if the kernel does not know their email yet, by
  a claim of their consent, sealed under their key; then ingests each of their turns as a
  source they wrote, sealed under their key (ADR 0022);
- runs `discover` on each export, and `conform` once no document or transcript is waiting
  to be mapped: conformance checks the log against the process the views describe;
- reports the work left: the sources no claim cites yet, the exports waiting, and the
  collections no configuration's views read. `work()` gives each waiting source's chunks,
  with their ids and words, for the session that maps them to cite.

Unchanged files are skipped, so a run over an unchanged folder with nothing left writes
nothing. Intake calls no model and writes no claim about what a source says: that is the
mapping's job, in a harness session with the skills that the consultant reviews.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from wmk_adapter.apply import Applier
from wmk_adapter.apply import Report as Applied
from wmk_adapter.plan import Plan, Source
from wmk_process import config, conform, discover

EXTRACTOR = "wmk-process intake"
EXTRACTOR_VERSION = "0.1.0"
MEDIA = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain"}
# A line opening a turn: a label of up to four capitalised words, a colon and a space.
LABEL = re.compile(r"^([A-Z][\w.'-]*(?: [A-Z][\w.'-]*){0,3}):(?:\s+|$)(.*)$")


class EngagementError(ValueError):
    """An engagement file, or a file it names, that intake cannot use."""


@dataclass(frozen=True)
class Person:
    key: str
    name: str
    email: str

    @property
    def node(self) -> str:
        return f"person:{self.email}"


@dataclass(frozen=True)
class Document:
    file: Path
    collection: str
    title: str
    uri: str
    origins: tuple[str, ...] = ()
    converted_from: Path | None = None


@dataclass(frozen=True)
class Session:
    file: Path
    collection: str
    consent: str
    speakers: dict[str, Person | None]


@dataclass(frozen=True)
class Turn:
    number: int  # its place in the transcript, counting every speaker's turns
    label: str
    text: str


@dataclass
class Engagement:
    path: Path
    name: str
    written: list[Document] = field(default_factory=list)
    told: list[Session] = field(default_factory=list)
    done: list[Path] = field(default_factory=list)

    def relative(self, path: Path) -> str:
        base = self.path.parent
        return path.relative_to(base).as_posix() if path.is_relative_to(base) else str(path)


def load(path: Path) -> Engagement:
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise EngagementError(f"{path}: {exc}") from exc
    if not isinstance(raw, dict) or not raw.get("name"):
        raise EngagementError(f"{path}: an engagement file needs a name")
    base = path.parent

    def file(entry: dict[str, Any], key: str = "file") -> Path:
        found = base / str(entry.get(key) or "")
        if not entry.get(key) or not found.is_file():
            raise EngagementError(f"{path}: no file {entry.get(key)!r}")
        return found

    def collection(entry: dict[str, Any]) -> str:
        value = str(entry.get("collection") or "")
        if not value:
            raise EngagementError(f"{path}: {entry.get('file')} needs a collection")
        return value

    people: dict[str, Person] = {}
    for key, spec in (raw.get("people") or {}).items():
        if not isinstance(spec, dict) or not spec.get("name") or not spec.get("email"):
            raise EngagementError(f"{path}: person {key!r} needs a name and an email")
        people[str(key)] = Person(str(key), str(spec["name"]), str(spec["email"]).lower())

    eng = Engagement(path, str(raw["name"]))
    for entry in raw.get("written") or []:
        source = file(entry)
        if source.suffix.lower() not in MEDIA:
            raise EngagementError(
                f"{path}: {entry['file']} is not Markdown or plain text; convert it first (markitdown or "
                "docling), name the converted file here and the original as converted_from"
            )
        eng.written.append(
            Document(
                source,
                collection(entry),
                str(entry.get("title") or source.stem),
                str(entry.get("uri") or eng.relative(source)),
                tuple(str(o) for o in entry.get("origins") or ()),
                file(entry, "converted_from") if entry.get("converted_from") else None,
            )
        )
    for entry in raw.get("told") or []:
        source = file(entry)
        if source.suffix.lower() != ".txt":
            raise EngagementError(
                f"{path}: {entry['file']}: transcripts are plain text, one turn per speaker label"
            )
        consent = entry.get("consent")
        if isinstance(consent, str):
            try:
                consent = date.fromisoformat(consent)
            except ValueError:
                consent = None
        if not isinstance(consent, date):
            raise EngagementError(
                f"{path}: {entry['file']} needs consent: the date (YYYY-MM-DD) its speakers agreed to be "
                "recorded"
            )
        speakers: dict[str, Person | None] = {}
        for label, key in (entry.get("speakers") or {}).items():
            if key is not None and str(key) not in people:
                raise EngagementError(
                    f"{path}: {entry['file']}: speaker {label!r} is {key!r}, who is not in people"
                )
            speakers[str(label)] = people[str(key)] if key is not None else None
        if not any(speakers.values()):
            raise EngagementError(f"{path}: {entry['file']} records no one: map a speaker to a person")
        eng.told.append(Session(source, collection(entry), consent.isoformat(), speakers))
    for entry in raw.get("done") or []:
        eng.done.append(file({"file": entry}))
    return eng


def turns(text: str, labels: Iterable[str], name: str = "transcript") -> list[Turn]:
    """The turns of a transcript: a line opening with a known label and a colon starts a turn,
    and the lines after it, up to the next label, continue it. Lines before the first label
    are a heading. A line opening with an unknown label is an error, so that a misspelt name
    never continues the previous speaker's turn."""
    known = set(labels)
    found: list[tuple[str, list[str]]] = []
    for n, line in enumerate(text.splitlines(), start=1):
        match = LABEL.match(line)
        if match and match.group(1) in known:
            found.append((match.group(1), [match.group(2)]))
        elif match:
            raise EngagementError(
                f"{name}, line {n}: unknown speaker {match.group(1)!r}; list it under speakers"
            )
        elif found:
            found[-1][1].append(line)
    out = []
    for number, (label, lines) in enumerate(found, start=1):
        words = "\n".join(lines).strip()
        if words:
            out.append(Turn(number, label, words + "\n"))
    return out


def plan(eng: Engagement) -> Plan:
    """The engagement's documents and turns as sources, with the people the turns record."""
    out = Plan(EXTRACTOR, EXTRACTOR_VERSION)
    for doc in eng.written:
        meta: dict[str, Any] = {"file": eng.relative(doc.file)}
        if doc.converted_from is not None:
            meta["converted_from"] = eng.relative(doc.converted_from)
            meta["converted_from_sha256"] = hashlib.sha256(doc.converted_from.read_bytes()).hexdigest()
        out.source(
            Source(
                alias=doc.collection,
                content=doc.file.read_text(),
                title=doc.title,
                uri=doc.uri,
                collection=doc.collection,
                media_type=MEDIA[doc.file.suffix.lower()],
                metadata=meta,
                origins=doc.origins,
            )
        )
    for session in eng.told:
        for person in {p.key: p for p in session.speakers.values() if p}.values():
            identity = {"email": person.email}
            out.node(person.node, type="Agent", kind="human", name=person.name, identity=identity)
            out.introductions.setdefault(
                person.node,
                f"{person.name} ({person.email}) agreed on {session.consent} to be interviewed and recorded "
                f"({session.collection}).",
            )
        for turn in turns(session.file.read_text(), session.speakers, eng.relative(session.file)):
            person = session.speakers[turn.label]
            if person is None:
                continue
            out.source(
                Source(
                    alias=f"{session.collection} turn:{turn.number}",
                    content=turn.text,
                    title=f"{session.collection}, turn {turn.number}",
                    uri=f"turn:{turn.number}",
                    collection=session.collection,
                    metadata={"file": eng.relative(session.file), "consent": session.consent},
                    subjects=(person.node,),
                    author=person.node,
                )
            )
    return out


@dataclass(frozen=True)
class Waiting:
    """A source no claim cites yet: what the mapping session needs to cite it."""

    collection: str
    uri: str
    title: str
    source_id: str
    author: str | None  # the speaker's agent id
    chunks: tuple[tuple[str, str], ...]  # (chunk id, its words)


@dataclass
class Export:
    config: str
    process: str
    discovered: Applied
    conformed: Applied | None = None
    waiting: str | None = None


@dataclass
class Result:
    name: str
    sources: Applied
    exports: list[Export]
    waiting: list[Waiting]
    unviewed: list[str]
    offset: int

    @property
    def unmapped(self) -> dict[str, list[str]]:
        """Collection -> the uris of its sources no claim cites yet."""
        out: dict[str, list[str]] = {}
        for w in self.waiting:
            out.setdefault(w.collection, []).append(w.uri)
        return out

    @property
    def failures(self) -> list[str]:
        out = list(self.sources.failures)
        for e in self.exports:
            out += e.discovered.failures + (e.conformed.failures if e.conformed else [])
        return out

    def text(self) -> str:
        lines = [f"Intake: {self.name}", f"Sources: {self.sources.summary()}"]
        for e in self.exports:
            lines.append(f"{e.config} ({e.process}):")
            lines.append(f"  discover: {e.discovered.summary()}")
            lines.append(f"  conform: {e.conformed.summary() if e.conformed else e.waiting}")
        if self.waiting:
            lines.append(f"Waiting to be mapped ({len(self.waiting)}):")
            lines += [f"- {c}: {', '.join(uris)}" for c, uris in self.unmapped.items()]
            lines.append(
                "Map them in a harness session with the core, process and interview skills, reviewing each "
                "claim, then run intake again. --work FILE writes their chunks for the session to cite."
            )
        else:
            lines.append("Nothing is waiting to be mapped.")
        for c in self.unviewed:
            lines.append(f"Note: no configuration's views list {c}, so compare and report leave it out.")
        for failure in self.failures:
            lines.append(f"  FAIL {failure}")
        return "\n".join(lines) + "\n"

    def work(self) -> str:
        """The sources waiting to be mapped, as Markdown for the mapping session: each chunk's id
        and words. Turns hold what people said: keep the file with the engagement's other
        private files."""
        out = [
            f"# Work left: {self.name}",
            "",
            f"Read at log offset {self.offset}. Each source below is stored; do not ingest it again. "
            "Map each chunk with the core skill and the process skill: cite its id as `claim.source`, "
            "quote its words, and have each claim reviewed before it is written. A turn's speaker is its "
            "author: claims about what they do name that agent. Run intake again when you are done.",
        ]
        for w in self.waiting:
            said = f", said by {w.author}" if w.author else ""
            out += ["", f"## {w.collection} {w.uri} ({w.title}): source {w.source_id}{said}"]
            for chunk, words in w.chunks:
                out += [
                    "",
                    f"Chunk {chunk}:",
                    "",
                    *(f"> {line}" if line else ">" for line in words.splitlines()),
                ]
        return "\n".join(out) + "\n"


async def run(client: Any, eng: Engagement, *, force: bool = False) -> Result:
    sources = plan(eng)
    applier = Applier(client, sources, force=force)
    applied = await applier.run()
    waiting: list[Waiting] = []
    offset = 0
    for source in sources.sources:
        source_id = applier.source_ids.get(source.alias)
        if source_id is None:
            continue
        result = json.loads(
            (await client.call_tool("query_log", {"source_id": source_id, "limit": 1})).content[0].text
        )
        offset = max(offset, int(result["head_offset"]))
        if result["entries"]:
            continue
        chunks = tuple(
            (chunk, source.content[start:end]) for start, end, chunk in applier.spans[source.alias]
        )
        author = applier.ids[source.author] if source.author else None
        waiting.append(Waiting(source.collection, source.uri, source.title, source_id, author, chunks))
    exports = []
    views: set[str] = set()
    for path in eng.done:
        cfg = config.load(path)
        views |= {c for collections in cfg.views.values() for c in collections}
        log = cfg.read()
        discovered = await Applier(client, discover.build(cfg, log)).run()
        export = Export(eng.relative(path), cfg.process, discovered)
        if waiting:
            export.waiting = f"waits until the {len(waiting)} sources above are mapped"
        else:
            try:
                model = await conform.read_model(client, cfg)
            except conform.ModelError as exc:
                export.waiting = f"waits: {exc}"
            else:
                export.conformed = await Applier(client, conform.build(cfg, log, model)).run()
        exports.append(export)
    collections = [d.collection for d in eng.written] + [s.collection for s in eng.told]
    unviewed = [c for c in dict.fromkeys(collections) if eng.done and c not in views]
    return Result(eng.name, applied, exports, waiting, unviewed, offset)
