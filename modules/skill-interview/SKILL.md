---
name: interview
description: Use when you are asked to interview a person about how their organisation works (who holds which post, who reports to whom, which team does what, how a piece of work flows) and to build the organisation and process maps in the Knowledge Kernel from the conversation.
---

# Interviewing a person to map the organisation and its processes

You talk with a person and record what they say as **extracted** claims: the organisation in W3C ORG,
its processes in BBO (the terms the process encoders use). Each claim cites the message it came from and
quotes the person's exact words. Then you ask about what the map still lacks. You use the
knowledge-kernel MCP tools under your own identity (see the knowledge-kernel skill for the write rules).

## The conversation is recorded first

Each message (yours and theirs) is recorded by the **interaction encoder** before you cite it: append it to
the interview's chat file (one JSON line: `{"id", "conversation", "author", "kind": "person" | "agent",
"time", "text"}`) and run `python -m interaction_encoder <file> --source <org slug>`, or let the harness
or the UI's chat do it. The person's message is then `urn:kk:message:<source>:<conversation>:<id>`, an
`as:Note` whose `as:content` is their text, recorded by the encoder, not by you. You cite it, so the quote
verifier can check your quote against it (no method verifies its own output).

## IRIs and terms

`<ORG>` is `http://www.w3.org/ns/org#`, `<BBO>` is `https://www.irit.fr/recherches/MELODI/ontologies/BBO#`:
write these as full IRIs (the kernel knows the CURIEs `rdf:`, `rdfs:`, `prov:`, `skos:` and `oa:`, not these; the
MCP read tools accept `org:` and `bbo:`). Slugs are lower case, words joined by `-`.

| The person says | Claims |
| --- | --- |
| the organisation ("at Acme") | `urn:kk:org:<org>` a `<ORG>FormalOrganization`; `rdfs:label` "Acme" |
| a team or department | `urn:kk:org:<org>:unit:<slug>` a `<ORG>OrganizationalUnit`; `rdfs:label`; `<ORG>unitOf` the organisation |
| a post ("the buyer", "procurement lead") | `urn:kk:org:<org>:post:<slug>` a `<ORG>Post`; `rdfs:label`; `<ORG>postIn` its unit |
| who holds it ("That's Lee Chen") | `urn:kk:person:<org>:<slug>` a `prov:Person`; `rdfs:label` "Lee Chen"; `<ORG>holds` the post |
| reporting ("Lee reports to me") | `<post>` `<ORG>reportsTo` `<the manager's post>` (posts report to posts) |
| a process | `urn:kk:process:<slug>`, its steps `<process>:step:<slug of the step's name>` with `rdfs:label` and `<BBO>has_container` the process (as in the process-extraction skill) |
| an order ("I check the budget code, then the buyer raises a purchase order") | `<A>` `<BBO>has_nextFlowNode` `<B>` |
| who does a step | `<step>` `<BBO>has_resource` `<post>`: the post, so the two maps meet |
| where it starts or ends ("every request lands in the inbox first", "then we're done") | `<process>:node:start` a `<BBO>StartEvent`, `has_nextFlowNode` the first step; the last step `has_nextFlowNode` `<process>:node:end`, a `<BBO>EndEvent` |

Look before you mint: `search("<name>")` or `query_claims(predicate="rdf:type", object_entity="org:Post")`,
and reuse an IRI that names the same thing. For a doubtful match record `skos:closeMatch` (it goes to review).

## Every claim

- `evidence_type: extracted`, one reference `{"source_uri": "<message IRI>", "quote": "<their exact words>"}`.
  The quote is copied from that one message, not paraphrased and not joined across messages.
- What you conclude rather than hear (a team is part of the organisation, two names are one person) is
  `inferred`, with `derived_from` the claims it rests on, in `"lane": "review"`: a person confirms it.
- Target scope `urn:kk:scope:default` unless the conversation is restricted.
- One change set per message you record from, idempotency key `interview:<org>|<message IRI>`.

## Ask next about the gaps

The map gap processor lists what the map lacks, as questions: `query_claims(predicate="oa:bodyValue")`,
keeping subjects that start with `urn:kk:gap:`; `get_entity(<gap>)` shows its kind and target. Kinds:
`no-performer` (who does a step), `no-next` and `no-previous` (what comes after or before it, or does the
process end or start there), `unheld` (who holds a post), `no-manager` (whom a post reports to),
`no-unit` (which team a post is in). Ask about one or two at a time, in plain words, starting with the
process the person is describing. A gap is retracted once its answer is recorded.

## Then

- `get_change_set(id)`: `applied`, or read the validation report and fix it.
- Tell the person what you recorded, citing claim ids with their belief, and what you could not place.
- Stop when the person wants to, or when no gap is left for what they described.
