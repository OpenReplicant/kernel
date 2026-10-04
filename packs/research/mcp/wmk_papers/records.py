"""Paper records, normalised identifiers, and the source a paper becomes in the kernel."""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from typing import Any

_DOI = re.compile(r"10\.[0-9]{4,9}/\S+", re.IGNORECASE)
_ARXIV_NEW = re.compile(r"([0-9]{4}\.[0-9]{4,5})(v[0-9]+)?$")
_ARXIV_OLD = re.compile(r"([a-zA-Z.-]+/[0-9]{7})(v[0-9]+)?$")
_ORCID = re.compile(r"([0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9Xx])")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def normalize_doi(value: str | None) -> str | None:
    """A bare, lower-case DOI from a DOI, a doi: string or a doi.org URL; None if there is none."""
    if not value:
        return None
    found = _DOI.search(value)
    return found.group(0).rstrip(".").lower() if found else None


def normalize_arxiv(value: str | None) -> str | None:
    """An arXiv id without its version suffix, from an id, an arxiv: string or an arxiv.org URL.

    arXiv's own DOIs (10.48550/arXiv.<id>) give the id too.
    """
    if not value:
        return None
    text = value.strip().rstrip("/")
    text = re.sub(r"(?i)^.*(arxiv\.org/(abs|pdf)/|arxiv[:.])", "", text).removesuffix(".pdf")
    for pattern in (_ARXIV_NEW, _ARXIV_OLD):
        found = pattern.search(text)
        if found:
            return found.group(1)
    return None


def normalize_orcid(value: str | None) -> str | None:
    found = _ORCID.search(value or "")
    return found.group(1).upper() if found else None


def clean_text(value: str | None) -> str | None:
    """Plain text from an abstract that may carry JATS or HTML markup and hard line breaks."""
    if not value:
        return None
    text = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", value))).strip()
    text = re.sub(r"^(abstract)\s*[:.]?\s+", "", text, flags=re.IGNORECASE)
    return text or None


@dataclass
class Author:
    name: str
    orcid: str | None = None


@dataclass
class Paper:
    title: str
    authors: list[Author]
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv: str | None = None
    abstract: str | None = None
    url: str | None = None
    found_in: str = ""
    cited_by: int | None = None
    references: list[str] = field(default_factory=list)

    def key(self) -> str | None:
        """The paper's identifier as the research skill uses it for a collection."""
        if self.doi:
            return f"doi:{self.doi}"
        if self.arxiv:
            return f"arxiv:{self.arxiv}"
        return None

    def ingest_arguments(self) -> dict[str, Any]:
        """The `ingest_source` arguments for this paper, as the research skill describes them:
        Markdown with the title, an authors line, venue and year, ids, then the abstract."""
        names = ", ".join(a.name for a in self.authors) or "Unknown authors"
        details = [names]
        if self.year:
            details.append(str(self.year))
        if self.venue:
            details.append(self.venue)
        if self.doi:
            details.append(f"doi:{self.doi}")
        if self.arxiv:
            details.append(f"arXiv:{self.arxiv}")
        abstract = self.abstract or "(no abstract)"
        content = f"# {self.title}\n\n{'. '.join(details)}\n\n## Abstract\n\n{abstract}\n"
        metadata = {
            k: v
            for k, v in {"doi": self.doi, "arxiv": self.arxiv, "year": self.year, "venue": self.venue}.items()
            if v is not None
        }
        uri = (
            f"https://doi.org/{self.doi}"
            if self.doi
            else f"https://arxiv.org/abs/{self.arxiv}"
            if self.arxiv
            else self.url
        )
        arguments: dict[str, Any] = {
            "content": content,
            "media_type": "text/markdown",
            "title": self.title,
            "metadata": metadata,
        }
        if uri:
            arguments["uri"] = uri
        if self.key():
            arguments["collection"] = self.key()
        return arguments

    def summary(self, abstract_chars: int = 600) -> dict[str, Any]:
        """The record for search results: the abstract shortened at a word boundary."""
        record = asdict(self)
        record.pop("references")
        if self.abstract and len(self.abstract) > abstract_chars:
            record["abstract"] = self.abstract[:abstract_chars].rsplit(" ", 1)[0] + " …"
        return record

    def full(self) -> dict[str, Any]:
        return asdict(self)
