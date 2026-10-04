# 0020. Reported claims quote their source; extraction happens in runs

Date: 2026-10-04 · Status: accepted

## Context

Two gaps in ingestion, both from the critical review of it:

1. A reported claim cited a chunk of about 1,500 characters, and nothing checked that the
   chunk says what the claim says. A model that invents a claim, or a source that injects
   one, could cite any real chunk and pass.
2. Re-reading a source (a better model, a newer skill, the full text after the abstract)
   added claims beside the old reading and retracted nothing. Under belief v1 the same
   source's newer assertion supersedes its older one only on the same edge, so facts the
   old reading got wrong and the new one omits stayed believed. The software adapter
   worked around this itself (ADR 0017); model extraction had no equivalent.

## Decision

**Quotes.**
- A claim may carry `quote`: the exact words of its cited chunk that it rests on, 8 to
  1,000 characters.
- `kernel.write` finds the quote in the source's text, starting near the chunk so that a
  quote running over the chunk's edge is found. Runs of whitespace, straight and curly
  quotation marks, hyphens and dashes, and letter case may differ; nothing else may. The
  matcher is a deterministic regular expression (`kernel.quote_pattern`,
  `kernel.find_quote`).
- The span is recorded with the claim (`quote_start`, `quote_end`, offsets into the
  source) in the log entry, and projected into `kernel.claims`.
- A quote that is not there is refused (`kernel.quote_in_source`). The rejection's
  `nearest` names the chunk's most similar sentence, so a retry can copy the real words.
- A new core rule, `core.reported_needs_quote` (provenance, `requires_quote`), makes the
  quote mandatory for `reported` claims. Packs can require quotes for other bases with
  the same parameter.
- Readers see the quoted words in `claims_view.quote` and in `query_log`, masked when the
  claim is redacted. The explorer shows them under each claim.

A quote proves the words exist, not that they mean what the claim says. Entailment needs
a model, so it belongs to a verifier agent that writes `verified_by`, not to the kernel.

**Extraction runs.**
- A run is an Event of the new core kind `extraction` (props: skill, model, extractor),
  created by a claim that cites the source the run reads. It starts `ongoing`.
- Claims name their run in `claim.run`. The kernel checks that the run is ongoing, that
  the writer started it, and that the claim cites the run's source: the same collection,
  or the same source when it has none (`kernel.run_agent`, `kernel.run_source`,
  `kernel.run_open`).
- `{"op": "close_run", "run"}` completes the run. In the same transaction the kernel
  takes this source's latest assertion on every edge and claim. Where that assertion is
  positive and came from an older run (one created before this run), the kernel adds a
  negative assertion from the same source. The source's newer assertion then supersedes
  the old one under belief v1.
- Claims written outside any run are never retracted by a run. A newer run's findings
  are never retracted by an older run that closes late.
- The input op is resolved before logging: the log stores the negative assertions and
  the run's transition to `completed`, never `close_run`. Invariant 4 holds, and replay
  reproduces the result.
- An agent that stops early transitions the run to `cancelled`; nothing is retracted.
- The software adapter now maps each changed file in a run and leaves retraction to the
  kernel. It cancels a run in which a claim was refused. Its git history stays outside
  runs, because history is append-only.

The kernel is version 0.3.0: reported claims without a quote, which 0.2 accepted, are now
refused.

## Consequences

Agents must quote. The core, interview and research skills say how; the eval fixtures
carry quotes for every reported claim; the write tool and the gateway's instructions
describe the field. A quote costs the extractor a few dozen tokens and spares a reader
from trusting the citation.

Runs make a reading of a source a unit: a better reading replaces a worse one. They also
make it a choice. Closing a run asserts that the run covered the whole source, so a run
over half a document must be cancelled, not closed. A weaker model's run closes over a
stronger one's just as readily, so compare readings first, in a fork (ADR 0018), before
closing a run in the main model.

Retraction by run is per source. A finding retracted from one paper keeps the `supports`
and `contradicts` edges other papers wrote about it until their own runs revisit them;
the explorer shows those edges pointing at a rejected claim.
