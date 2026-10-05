# 0022. Erasing personal data: keys per subject, an erasure ledger, re-projection

Date: 2026-10-04 · Status: accepted, built in kernel 0.5.0 (2026-10-05). An operator role
stands in for the approval channel until ADR 0019 is built.

## Context

The log is append-only (invariant 2) and the graph is its exact projection (invariant 3).
A person can still ask to be forgotten, and the law may require it. Redaction (ADR 0006)
only masks reader views: the words stay in the log, in source content and in backups,
readable by the database owner. The design names the way out ("source content is
encrypted per source; erasure deletes the key, and replay projects those fields as
redacted").

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
  ciphertext forever and stays append-only. Destroying a key makes its ciphertext
  unreadable everywhere, backups of the log included. Chosen.

## Decision

**Subjects.** A data subject is a person the kernel holds data about, named by their Agent
id.
- `ingest_source` takes `subjects`: the people a source is from or about. The default is
  its author, when the author is a human agent. `subjects: []` declares content about no
  one.
- A human Agent (kind `human`) is always its own subject.

**Keys.** `kernel.data_keys` sits outside the log and holds 32 random bytes per key. Only
the kernel owner reads it, through SECURITY DEFINER functions. It is the one kernel table
rows are deleted from.
- A source with subjects gets a key with the source's id. Its subjects are the source's.
- A human agent gets a key with the agent's id, at the write that creates it.
- Erasing a subject destroys:
  - their own key;
  - the key of every source listing them among its subjects;
  - the keys of any further sealed sources the request names.

  Over-erasure is deliberate: a transcript of a meeting goes as a whole.

**What is sealed.** Sealing happens at write time (pgcrypto, AES-256-CBC, a random IV per
value). A sealed value reads `wmk:sealed:<key id>:<base64>`.
- **Sources with subjects:** content, title and metadata, and each chunk's text and
  heading. The uri and collection stay readable, so they must be opaque (a session id,
  never a name). The content hash stays too, so known content is still skipped.
- **Claims:** the text is sealed under its source's key when the source has subjects, else
  under the key of the first human agent the claim creates. This covers "Sam joined the
  team". The log and `kernel.claims` hold the sealed value, and `claims.text_key` names the
  key.
- **Creates of a human agent:** name, aliases, identity, props and embedding, as one sealed
  JSON document in the operation (`"sealed"`).

**Projection.** `kernel.open_op` and `kernel.unseal` decrypt while projecting, which is
deterministic for a given set of keys.
- Opened values go into the projection: nodes keep plain names, so resolution (identity
  keys, trigrams, embeddings) still finds a sealed person.
- `nodes.sealed_key` records which key a node's fields came from.
- Where a key is gone, a person's name projects as `[erased]`, with no aliases, identity,
  props or embedding. A Claim node's text projects as `[erased]`.
- Invariant 3 now reads: the graph is a function of the log and the keys that remain.

**Reads.** Readers open sealed values through views: `claims_view`, `sources_view`,
`chunks_view` and `log_entries`. They show `[erased]` once a key is gone, with the quote
NULL and an `erased` flag. Writes refuse to cite an erased source, and `ingest_source`
refuses to store erased content again.

**Erasure.** `kernel.erase` is a fourth function that changes data, which amends
invariant 1.
- It runs only as `kernel_eraser`. That is an operator acting on an approved request,
  never the gateway, never an agent: erasure destroys evidence.
- It takes the log's append lock, so no write seals or opens with a key while the key goes.
- In one transaction it:
  1. deletes the keys;
  2. re-projects the nodes they sealed (`kernel.erase_nodes`, which produces exactly what
     a replay without those keys produces, redactions included);
  3. appends a row to `kernel.erasures`.
- The ledger row holds the subject, keys, sources, nodes, who requested and approved,
  reason, log offset and time. The ledger is append-only.
- The log is untouched, and replay reproduces the erased graph.

**Approval, for now.** The request must name `requested_by` and `approved_by`, and the
ledger records both. Until the approval channel (ADR 0019) exists, the operator holding
`kernel_eraser` is trusted to have checked them. `python -m kernel.erase`
(`make erase-scope`, `make erase`) is the operator's tool.

**Review before erasing.** `kernel.erasure_scope(subject)` lists:
- the keys;
- the sealed sources;
- how many claims those keys sealed;
- the nodes that would be re-projected;
- `derived_nodes`: unsealed nodes created by those claims, such as an entity named after
  the person;
- `not_covered`: claims in the clear that touch the person's node but cite other sources
  or none;
- earlier erasures of this subject.

The approver adds sealed sources to the request, or follows up with redaction or
retraction for what keys cannot reach.

**Structure stays.** Erasure turns a person into `[erased]`. It does not delete the node,
its edges or its belief: "someone held the approver role from March" survives. Where that
is still identifying, the approver can also retract the edges (ordinary negative
assertions).

**Not reached by keys.** These stay readable after erasure. The scope lists the first three
so they can be redacted or retracted:
- claims about a person that cite unsealed sources, or none;
- entities and edge props written from sealed sources;
- `unresolved` reasons;
- cite sentences.

Data ingested before kernel 0.5.0 sits in the log in the clear. It can only be erased by
rewriting the log once.

**Outside the database.**
- Telemetry carries only ids (invariant 9).
- After erasing, `python -m kernel.erase` runs VACUUM FULL on the key table, the nodes and
  the graph's Agent and Claim vertex tables. Neither the keys nor the old row versions then
  stay in their pages.
- WAL archives and backups hold the keys and the old projection until they expire, so
  their retention bounds erasure. Back up `kernel.data_keys` apart from the log, with a
  shorter retention.
- A key-encryption key held outside the database and rotated would tighten this bound.
  The projection must not make network calls, so that key would have to reach the
  database another way. That is the next step if backup retention is not enough.
- With an embedding service configured (ADR 0009), the gateway sends a person's name to it
  to be embedded. Any copy the service keeps is outside the kernel's reach.
- Eval reports, exports and explorer caches are rebuilt or deleted by the operator. The
  ledger lists what was erased so they can be.

## Consequences

The log stays append-only and replay stays exact. An erasure costs one row deletion per
key, a re-projection of the person's node and of the Claim nodes from their sources, and a
VACUUM.

The costs:
- encryption on every write that touches a subject;
- decryption on reads of sealed rows;
- a key table that must be backed up and protected on its own;
- a fourth function that changes data.

The scheme gives confidentiality, not tamper evidence: CBC is not authenticated, and the
keys sit in the same database as the ciphertext. Erasure, not access control, is the goal.
Readers already see the clear text through the views.

Remaining work:
1. The approval channel's authenticated person (ADR 0019), to replace the operator's say-so.
2. A key-encryption key outside the database, if backup retention is too long a bound.
