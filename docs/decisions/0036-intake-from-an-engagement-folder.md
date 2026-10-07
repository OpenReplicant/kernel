# 0036. Intake from an engagement folder

Date: 2026-10-07 · Status: accepted (builds ADR 0035's first step)

## Context

ADR 0035 puts intake first: a client's documents, interview transcripts and exports go
into the kernel with one command. Until now, Northwind's views came only from a scripted
fixture, which ingests each interview turn from a file of its own. `make northwind` ran
`discover` and `conform` on the export by hand.

A client's folder looks different:
- **Transcripts:** interviews arrive as transcripts, one file per session, with speaker
  labels. The interviewer's turns are mixed in.
- **People:** the people in a transcript must exist as agents before their words are
  stored, so that their words are sealed under their own keys (ADR 0022). Their words are
  stored only with their consent.
- **Documents:** they arrive as PDF or Word files.
- **Mapping needs judgement.** Until the extractor (ADR 0024) meets its floors, a harness
  session with the skills turns documents and transcripts into claims, and the consultant
  reviews each one. Intake has to say which sources still wait for that, and give the
  session what it needs to cite them.

Intake is also the first adapter that is plainly an encoder. The owner suggested sorting
adapters into encoders and decoders, and the kinds built so far follow different rules,
which no document states yet.

## Decision

**Adapters by direction.** "Adapter" stays the name in code. The directions are for design
and review, and each has its own contract:
- **Encoders** turn what they read into sources and claims: intake, `discover`, `conform`,
  the Docker and forge captures, the papers server, and later the extractor.
  - They write only through the gateway.
  - Each claim cites what it read.
  - An unchanged input writes nothing new.
  - People are listed as subjects, so they are sealed.
  - A deterministic encoder writes observed claims. A model encoder (the extractor, or a
    harness with the skills) writes reported claims that quote their source.
- **Decoders** read the kernel at one log offset and write nothing (at most `cite`):
  `compare`, `rank`, `report`, `forge audit`, `drift`, the explorer, and later recall.
  - Every finding cites the claims it rests on.
  - Erased words stay erased.
  - Claims are attributed to collections, never to names.
- **Actuators** change a system outside the kernel. None is built yet; the workflow
  adapters will be the first.
  - They act only on a change a person approved (invariant 10).
  - They hold no credential that bypasses that approval.
  - Each comes paired with an encoder that reads back what it did.

The report's citation code serves decoders in general. It moves into the adapter kit when
a second pack needs it, which ADR 0035's step 3 will.

**The engagement file.** One YAML file at the top of the engagement folder, with paths
relative to it (`wmk-process intake` documents the format):
- **`people`:** the people the transcripts record, each with a name and an email. The email
  is their identity, so a second session or the mapping finds the same agent.
- **`written`:** documents, each with a collection, a title and, optionally, a uri, origins
  and `converted_from`.
- **`told`:** transcripts, each with a collection, the date its speakers consented to be
  recorded, and its `speakers`, which map labels to people. A label mapped to no one, such
  as the interviewer, is not recorded.
- **`done`:** wmk-process configurations, one per export, as before.

Collections and uris never hold names (ADR 0022). People are named only under `people`.

**Transcripts** are plain text:
- A line that opens with a known label and a colon starts a turn. The lines after it
  continue the turn, up to the next label.
- Lines before the first label are a heading.
- A line that opens like a label, but with a label the file does not list, is refused. So
  a misspelt name is never stored as part of the previous speaker's turn.
- **Each turn of a person is one source:**
  - its content is the turn's words, ending in a newline;
  - its uri is `turn:N`, its place in the transcript counting every speaker;
  - its collection is the session's, and its title names the collection and the turn;
  - its author is the person, and so is its subject.
- Other formats (WebVTT, meeting-tool exports) are further readers that produce the same
  turns.

**Consent.** A session without a consent date is refused. A person the kernel does not
know yet is created by a claim recording their consent: "Maya Chen (maya.chen@…) agreed on
2026-09-15 to be interviewed and recorded (interview:nw-2026-09-15-a)." The claim is sealed
under the person's key, as the person is. Each turn also carries the consent date in its
metadata, which is sealed.

**Documents.**
- **Text and Markdown** are ingested as they are.
- **PDF and Word files** are converted at the edge first, with markitdown or docling. Both
  are MIT-licensed, which passes ADR 0012. The engagement file names the converted file,
  with the original as `converted_from`, and intake records the original's name and SHA-256
  in the source's metadata.
- **Any other file type** is refused, with a message naming the converters.
- **Why intake does not convert:** the kernel checks each quote against the text it stored.
  That text should be what the consultant reviewed, not the output of whichever converter
  intake happened to run.

**Exports.**
- Intake runs `discover` on each export at once, since the log needs no mapping.
- It runs `conform` once no document or transcript is waiting to be mapped. Conformance
  checks the log against the process the views describe; run earlier, it would check the
  log against its own map.

**The work left.** Intake prints:
- the sources no claim cites yet, by collection and uri (never by title);
- the exports waiting for them;
- any collection that no configuration's views list, which compare and report would
  ignore.

With `--work FILE`, it also writes each waiting source's chunks to that file as Markdown:
- the chunk id and its words;
- the source id;
- for a turn, the speaker's agent id.

The mapping session cites those ids and does not ingest again. Ingesting again would
matter: the kernel only recognises a source with the same collection, uri and content, so
a turn ingested with different whitespace becomes a second source, and the first one waits
forever. The file holds what people said, so it stays with the engagement's private files.

**Running intake again.**
- After the mapping, the next run checks conformance and the list empties.
- Over an unchanged folder with nothing waiting, intake writes nothing.
- A changed file is a new version in its collection, and is listed until it is mapped
  again.

**The mapping is not intake.** A harness session with the core, process and interview
skills writes the claims, reviewed by the consultant. Following the core skill, it looks up
each node before creating it, so it meets the people intake created and the nodes
`discover` created.
- **For Northwind,** the northwind-views script stands in for that session, as before.
- **Fixture scripts gain `reuse: true`.** Each create first looks up a node with the same
  identity, or (without one) the same kind and normalised name, and reuses it, as `apply`
  does. A write whose operations were all such creates is skipped. Runs are always new.
  Without the flag, a script behaves as before.

**`make northwind`** runs:
1. intake;
2. the scripted mapping;
3. intake again;
4. the report.

`make seed` now counts a fixture as seeded once a claim cites its first source. A source
that is merely known may be one intake stored for mapping. CI runs `make northwind` before
`make seed`, so it takes the path an engagement takes: intake first, on an empty stack.

## Consequences

**Process pack:**
- `wmk-process intake ENGAGEMENT [--work FILE]`;
- Northwind's engagement folder, `packs/process/engagements/northwind`. It holds two
  transcripts and points to the fixtures' SOP and export. A test checks that its turns
  match the fixture's turn files.
- The fixture's turn uris now give their place in those transcripts (`turn:2`, `turn:4`,
  …), since the kernel matches a stored source by collection, uri and content.

**Adapter kit:**
- a source can name its author, who must be one of its subjects;
- a plan can give the claim that creates a person (here, their consent);
- the applier keeps each source's id.

**Evals:** the player's `reuse`, and seed's check.

**The kernel does not change.**

**Tests:**
- Northwind runs through intake, the scripted mapping and intake twice more. The third run
  writes nothing.
- The resulting report equals the report from the old order (views, then the log), apart
  from log offsets.
- Turns are sealed under their speakers' keys, consent claims name each person once, and
  neither the printed summary nor the work list's headings name anyone.

**Limits:**
- **Formats:** plain-text transcripts only.
- **Questions:** the interviewer's questions are not stored, so a short answer ("Yes") is
  mapped without its question in the kernel. The work file and the transcript still show
  it to the person mapping.
- **Consent:** one consent date per session, for all its speakers.
- **Identity:** a person is found again only by email.
- **Conversion:** documents are converted outside intake.
- **Waiting:** conformance waits for every source in the folder, not just its own views.
