# 0022. Erasing personal data: keys per subject, an erasure ledger, re-projection

Date: 2026-10-04 · Status: proposed (design only; nothing here is built until reviewed)

## Context

The log is append-only (invariant 2) and the graph is its exact projection (invariant 3).
A person can still ask to be forgotten, and the law may require it. Redaction (ADR 0006)
only masks reader views: the words stay in the log, in source content and in backups,
readable by the database owner. The design names the way out ("source content is
encrypted per source; erasure deletes the key, and replay projects those fields as
redacted"), but nothing is built. Until something is, no real interview or business
document should be ingested.

Personal data reaches the kernel in five places:

1. source content, chunk text, titles, URIs and metadata;
2. claim text, and the words a quote points at;
3. node fields of a person (a human Agent): name, aliases, identity (an email), props and
   the embedding computed from them;
4. edges that say something about a person (Sam holds the approver role);
5. copies outside the database: backups, eval reports, exports, browser caches.

## Options

- **Rewrite the log** (excision, as in Datomic or Kafka compaction). This is the most
  complete option, but it breaks the append-only guarantee and every reference by offset
  (`read_at_offset`, cites, as-of reads). Rejected.
- **Keep personal data out of the kernel**: content in an external store that can delete,
  only pseudonymous ids in the log. Claim text and person names are personal data
  themselves ("Sam approves invoices"), so the kernel would lose the very thing it records.
  Rejected as the main mechanism, kept as advice for raw media.
- **Encrypt per data subject and destroy keys (crypto-shredding).** The log keeps
  ciphertext forever and stays append-only; destroying a key makes its ciphertext unreadable
  everywhere, backups of the log included. Chosen.

## Decision (proposed)

**Subjects.** A data subject is a person the kernel holds data about, identified by their
Agent id.

- `ingest_source` takes `subjects`: the people a source is from or about. The default is
  its author agent.
- A human Agent is always its own subject.
- Collection names must be opaque, such as session ids. They are used in the clear for
  belief, so they must never contain a name.

**Keys.**
- Each source with subjects gets a data key. So does each human Agent.
- Keys live in `kernel.subject_keys`, a table outside the log. Only the kernel owner can
  read it, through SECURITY DEFINER functions. It is the one kernel table where rows are
  deleted.
- Erasing a subject destroys:
  - their agent key;
  - the key of every source that lists them among its subjects (over-erasure is
    deliberate: a transcript of a meeting goes as a whole).

**What is encrypted in the log.** `kernel.write` and `kernel.ingest_source` encrypt at
write time (pgcrypto, symmetric, random IV). The fields:

- **Sources:** content, title, uri and metadata, under the source's key. Chunks hold
  offsets only, and their text is projected.
- **Claims citing such a source:** their text, under the source's key.
- **Creates of a human Agent:** name, aliases, identity, props and the embedding, under
  the agent's key.

Randomness at write time is allowed; projection only decrypts, which is deterministic for
a given key. Invariant 3 then reads: the graph is a function of the log and the erasure
ledger.

**Projection.** The projection decrypts into the projection tables, where resolution keeps
working on plain names. Where a key is gone, it writes the fields as `[erased]`. Nothing
else changes:
- ids and offsets;
- edges and assertions;
- belief, since erasure is not retraction;
- quote spans (the quoted words read as NULL).

**The erasure ledger.**
- `kernel.erasures` is append-only. It holds subject, sources, who requested and who
  approved, the offset, and the time.
- It is written by `kernel.erase`, a fourth write function. That is a change to
  invariant 1, by this ADR.
- `kernel.erase` runs as a dedicated `kernel_eraser` role, never the gateway's writer.
  Requests come from an authenticated person through the approval channel (ADR 0019).
  An agent can never erase, because erasure destroys evidence.
- In one transaction it destroys the keys, records the ledger entry, and re-projects every
  row that read them: nodes, claims, sources, chunks and the AGE mirror.
- Replay then reproduces the erased graph exactly.

**Review before erasing.** For a subject, `kernel.erasure_scope(subject)` lists:
- their sources;
- claims citing those sources;
- claims whose operations touch the person's node but cite other sources (an org chart
  naming them).

The approver adds those sources to the request or leaves them; the ledger records the
choice.

**Structure stays.** Erasure turns a person into `[erased]`. It does not delete the node,
its edges or its belief: "someone held the approver role from March" survives. Where that
is still identifying, the approver can also retract the edges (ordinary negative
assertions) in the same request.

**Outside the database.**
- Telemetry carries only ids (invariant 9).
- Backups keep a destroyed key until they expire, so the backup retention period bounds
  erasure. Keys must be backed up separately from the log, with a shorter retention.
- Eval reports, exports and explorer caches are rebuilt or deleted by the operator; the
  ledger lists what was erased so they can.

## Consequences

The log stays append-only and replay stays exact; erasure costs one row deletion per key
and a re-projection.

There are three costs:
- encryption on every write that touches a subject;
- a key table that must be backed up and protected on its own;
- no Cypher or trigram search over ciphertext in the log (searches run on the projection,
  as now).

Data ingested before this exists sits in the log in the clear. It can only be erased by
rewriting the log once, before production, so this must be built before real personal data
arrives.

Prerequisites, in order:
1. The approval channel's authenticated person (ADR 0019), so an erasure has an approver
   who is not the system.
2. `subjects` at ingest.
3. Encryption in the write path.
4. `kernel.erase`, the ledger, and the scope query.
5. A replay test that erases mid-log.
