# Kernel as a content system

**Status: proposed framing (D-101, owner, 2026-11-08). No schema change.** This page says what kernel is, in the
terms people and agents already work in, and what a thin layer on top would add. The invariants
(`CLAUDE.md`, `docs/spec.md`) are unchanged and apply to everything here.

## In one line

Kernel is an **ontological content system**: content types from ontologies, items with revisions, review before
anything lands, and permissions enforced by Postgres. Claims are one content type, the assertion, and also the
index derived from every other type. Encoders bring content in, processors and triggers react to it, and agents
work on it through MCP.

## Why reframe

Kernel was first a graph, then a claim log with extraction, with open RDF vocabularies. Claims are the right unit
for checking: provenance per statement, conflicting sources side by side, belief, verification, drift. They are
the wrong unit for **working in**. People and language models read and write documents with some structure:
fields plus prose. When the only interface is claims, every consumer rebuilds the document itself:

- a drift gap is five claims, and a reader has to find the one sentence among them (`oa:bodyValue`);
- working memory has to be squeezed from a set of claims into a 2,000-character summary;
- a catalog entry is a dozen claims that a viewer reassembles into the entry it came from.

Each of these is a document with a type. The reframe makes that the unit agents and people see, and keeps claims
as what the machinery checks.

## What kernel already has, in content terms

| Content-system concept | Kernel today |
| --- | --- |
| a content type | a class in a vocabulary pack (`ontology/`), with a SHACL shape for its fields |
| an item | an entity (an IRI, such as `urn:kk:node:model-gateway`) |
| an item's content, one version | an artifact (`kernel.artifact`): content-addressed (sha256), scoped, attributed, redactable |
| the current version | a `kkt:hasArtifact` claim from the item to `urn:kk:artifact:<sha256>`; a new version supersedes it, so the history is the claim's history |
| an item's fields | deterministic claims from a structured artifact (as `encoder-backstage` does with a catalog file, D-100) |
| a citation into content | `kernel.claim_reference`: quote, prefix, suffix, span |
| draft, review, publish | change sets: `submitted`, `valid`, `awaiting_review`, `approved` or `rejected` (D-24) |
| who may see and change what | scopes and clearance, enforced by row-level security as the caller's own role |
| who changed it | `attributed_to`, `generated_by` (a recorded run), `decided_by` |
| deletion | none: supersede, refute or retract by status; redaction keeps identity and lineage |
| webhooks and automations | encoders and processors under their own roles, LISTEN plus sweep (`kk.lib`), and the bus where one is wired |
| API users | agents and people, each with an own login, through kernel-mcp (D-37, D-44) |

## What stays kernel's own

Kernel was not built like a CMS, and these differences are the reason to keep building it rather than adopt one:

| | A typical open-source CMS | Kernel |
| --- | --- | --- |
| Access | checked by the application, which connects to the database as one user | each caller is a Postgres role; row-level security enforces it |
| Change | edit in place, versions on the side, delete is normal | change sets and review; nothing is deleted |
| Truth | one current value per field | conflicting claims side by side; belief and fusion pick a current value |
| Provenance | who edited the item | who said each statement, from which quote in which artifact |
| Schema | content types in application config, closed world | RDF classes mapped across vocabularies (SKOS, `ontology/mappings.ttl`), open world |
| Agents | API clients | first-class users with their own logins and clearance |

## The layer this adds

Small, and none of it changes `kernel/schema/`:

1. **Content types as packs.** A content type is a class plus a SHACL shape in a vocabulary pack. The shape lists
   the fields (property, datatype or class, cardinality) and is what the validator already checks.
2. **One view per content type, generated from its shape.** `api.items_<type>` (`security_invoker = true`, so
   row-level security applies as the reader): one row per item, one column per field, from current claims. Any
   read-only tool can then browse kernel in the shape people expect: kernel's UI, a SQL client, a reporting tool.
3. **Item functions in `api`, and the same as MCP tools.** `get_item(iri)`, `list_items(type, filter)`,
   `item_history(iri)`, and `put_revision(iri, type, content)`, which stores the artifact and submits one change
   set: the `kkt:hasArtifact` claim plus the field claims the shape derives. It goes through the lane like any
   other change set, so review and clearance apply unchanged.
4. **A generic items view in the UI** (views are modules, D-44): a list and a form for any type, generated from
   its shape; a save is `put_revision`.

**Conversions** are the encoders kernel already has. A structured revision becomes deterministic claims; prose
becomes extracted claims, each quoting its span; observations stay observations, since a measurement is not
content anyone wrote.

## Off-the-shelf dashboards

Postgres has many web front ends: Directus, NocoDB, Baserow, Teable, Appsmith, Budibase, and admin frameworks such
as react-admin and Refine. Almost all of them hit two walls here:

- **Identity.** They connect as one database user and enforce permissions in the application, so a person's own
  clearance would not apply, and every change would be attributed to the dashboard.
- **Writes.** Their editors insert, update and delete rows. Kernel's tables are append-only; writes go through
  `api.submit_change_set` and `api.review`.

Licences rule out several under D-11 (read 2026-11-08, to be checked against each LICENSE file before any use):
Directus moved to the Monospace Sustainable Core License, NocoDB to the Sustainable Use License, Budibase is GPL.

The one route that keeps per-person identity is PostgREST, which switches to the caller's role from their token,
under a generic admin front end such as react-admin (MIT) with a PostgREST data provider, with custom actions for
writes. That is the optional `api-adapter` slot D-42 left open. The per-type views (2) make it cheap to add later;
the default stays one API, kernel-mcp.

## How it is proven

The framing earns its place only if it lets something be deleted or made simpler. The first content types:

| Content type | Replaces | Proof |
| --- | --- | --- |
| catalog entry | reassembling an entry from catalog claims (Substrate's spec step 13) | an entry is read and edited as an item; its claims and drift checks are unchanged |
| session brief | the working-memory summary squeeze (M6-T2) | a session reads and rewrites one brief; on held-out sessions it states goal and focus at least as well |
| drift gap | finding the sentence among an annotation's claims | the review lane shows a gap as an item with its two sides |

If those come out with less code and no loss on their checks, the framing becomes how kernel describes itself;
otherwise this page is withdrawn.

## Open questions

- **Drafts.** A change set waiting for review is a draft today; whether an item also needs a private draft state
  before it is submitted.
- **Body format.** Markdown for prose bodies, with spans as character offsets, is the proposal; it needs checking
  against `claim_reference.span` as extractors use it.
- **The revision link.** `kkt:hasArtifact` is the self-encoder's link to a file, but its range is TOSCA's
  `ArtifactDefinition`; an item's version may need a standard term instead (`dcterms:hasVersion`, or PROV's
  `prov:wasRevisionOf` between versions), proposed through `propose_term` (D-87).
- **Authored assertions.** A person writing a claim directly (testimony) and a claim derived from content are both
  claims; whether the facade shows them as one type or two.
