# 0025. Full text through GROBID, in the abstract's collection

Date: 2026-10-05 · Status: accepted

## Context

Research is mapped from abstracts, which overstate results and leave out their conditions.
CLAUDE.md approves a parser container (GROBID or Docling) once a live eval shows extraction
quality. The annotated-corpus eval (`make annotated`, results in `evals/annotated/RESULTS.md`)
now measures it on abstracts. The adapter survey names
docling-serve for general documents and GROBID for papers.

Docling's layout models are fetched from Hugging Face. GROBID's CRF models ship inside its
image.

## Decision

- **The parser.** GROBID 0.9.1 with its CRF models only (no GPU, about 500 MB), pinned by
  digest, runs in the research profile beside the papers server. It is not published on
  the host. The kernel never talks to it.
- **The tool.** The papers server gains `get_full_text` (DOI, arXiv id or PDF URL). It:
  1. looks the record up;
  2. fetches the open-access PDF (the arXiv PDF, OpenAlex's best open-access location, or
     the URL given; at most 50 MB);
  3. has GROBID parse it;
  4. returns `ingest_source` arguments.

  It holds no database credentials (ADR 0014), and the agent ingests what it returns.
- **The Markdown** follows the research skill's layout:
  - the record's header (authors, year, venue, ids), which is cleaner than a parsed title
    page;
  - the abstract;
  - the body, with numbered sections as headings (`3.1` one level below `3`);
  - figure and table captions;
  - the references.

  The kernel's Markdown chunker gives every chunk its heading path, and `terms` its
  abbreviations, so a chunk deep in the Results still reads in context.
- **Same collection, new version.** The full text keeps the abstract's collection
  (`doi:` or `arxiv:`) and origins. It is a newer version of the same source, so a run over
  it closes over the abstract's run (ADR 0020): findings the full text does not support are
  retracted. Belief never counts the two versions as two sources.
- `metadata.parser` records `GROBID 0.9.1`, so a re-parse with a better parser is
  visible.

## Consequences

Papers with an open-access PDF can be mapped from their full text. Paywalled papers stay
abstract-only unless someone with access passes a PDF URL. GROBID handles born-digital
PDFs well; scanned PDFs need OCR, which this does not do. Tables are kept only as captions.

The tests use a recorded TEI document parsed from a synthetic paper, so CI needs neither
GROBID nor the network. `make up-research` starts it.

A full text costs more to map than an abstract, often ten times the text. `write_batch`
(ADR 0023) keeps the number of calls down.
