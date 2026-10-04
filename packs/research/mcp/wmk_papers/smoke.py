"""`make papers-smoke`: one live lookup per paper source, to check the APIs and the record
mapping against real responses. Needs network access to the APIs; not run in CI."""

from __future__ import annotations

import os
import sys

from wmk_papers.sources import ArxivSource, CrossrefSource, OpenAlexSource, Source

# Well-known papers: "Attention Is All You Need" on arXiv, and "Deep learning" (Nature, 2015).
LOOKUPS: list[tuple[str, str | None, str | None]] = [
    ("arxiv", None, "1706.03762"),
    ("crossref", "10.1038/nature14539", None),
    ("openalex", "10.1038/nature14539", None),
]


def main() -> None:
    mailto = os.environ.get("WMK_PAPERS_MAILTO") or None
    sources: dict[str, Source] = {"arxiv": ArxivSource(), "crossref": CrossrefSource(mailto)}
    if key := os.environ.get("WMK_PAPERS_OPENALEX_API_KEY"):
        sources["openalex"] = OpenAlexSource(key, mailto)
    failed = False
    for name, doi, arxiv_id in LOOKUPS:
        if name not in sources:
            print(f"{name:<9} skipped (no WMK_PAPERS_OPENALEX_API_KEY)")
            continue
        try:
            paper = sources[name].get(doi, arxiv_id)
            hits = sources[name].search("attention is all you need", 3, None)
        except Exception as exc:
            failed = True
            print(f"{name:<9} FAILED {type(exc).__name__}: {exc}")
            continue
        print(
            f"{name:<9} ok  {paper.title[:60]!r} ({paper.year}), {len(paper.authors)} authors, "
            f"abstract {'yes' if paper.abstract else 'no'}, search returned {len(hits)}"
        )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
