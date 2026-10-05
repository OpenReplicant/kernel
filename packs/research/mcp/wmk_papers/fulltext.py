"""Full text: a paper's PDF, parsed by GROBID, as Markdown the kernel can ingest.

GROBID (Apache-2.0, a JVM service in its own container) turns a PDF into TEI XML. Here the
TEI becomes Markdown in the research skill's layout: the title, an authors line, venue,
year and ids, the abstract, then the body's sections as headings (numbered sections keep
their numbers, `3.1` one level below `3`), figure and table captions, and the reference
list. Nothing here calls a model.
"""

from __future__ import annotations

import re
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from wmk_papers.records import Author, Paper

NS = {"tei": "http://www.tei-c.org/ns/1.0"}
MAX_PDF_BYTES = 50_000_000
_SPACE = re.compile(r"\s+")


class FullTextError(Exception):
    """The PDF could not be fetched, or GROBID could not parse it."""


@dataclass
class Section:
    heading: str
    level: int
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class FullText:
    title: str
    authors: list[Author]
    abstract: str
    sections: list[Section]
    captions: list[str]
    references: list[str]
    parser: str
    doi: str | None = None


def text(node: ET.Element | None) -> str:
    return _SPACE.sub(" ", "".join(node.itertext())).strip() if node is not None else ""


def person(node: ET.Element) -> str:
    names = node.find("tei:persName", NS)
    if names is None:
        return ""
    parts = [text(f) for f in names.findall("tei:forename", NS)] + [text(names.find("tei:surname", NS))]
    return " ".join(p for p in parts if p)


def from_tei(xml: str) -> FullText:
    root = ET.fromstring(xml)
    header = root.find("tei:teiHeader", NS)
    app = root.find(".//tei:encodingDesc/tei:appInfo/tei:application", NS)
    parser = f"{app.get('ident', 'GROBID')} {app.get('version', '')}".strip() if app is not None else "GROBID"
    analytic = (
        header.find(".//tei:sourceDesc/tei:biblStruct/tei:analytic", NS) if header is not None else None
    )
    authors = []
    for a in analytic.findall("tei:author", NS) if analytic is not None else []:
        name = person(a)
        orcid = a.find("tei:idno[@type='ORCID']", NS)
        if name:
            authors.append(Author(name=name, orcid=text(orcid) or None))
    doi = root.find(".//tei:sourceDesc//tei:idno[@type='DOI']", NS)
    abstract = " ".join(text(p) for p in root.findall(".//tei:profileDesc/tei:abstract//tei:p", NS))
    sections = []
    body = root.find("tei:text/tei:body", NS)
    for div in body.findall("tei:div", NS) if body is not None else []:
        head = div.find("tei:head", NS)
        number = head.get("n", "") if head is not None else ""
        heading = " ".join(p for p in (number, text(head)) if p) or "Text"
        level = 2 + number.rstrip(".").count(".") if number else 2
        section = Section(heading, min(level, 6))
        section.paragraphs = [t for p in div.findall("tei:p", NS) if (t := text(p))]
        if section.paragraphs or head is not None:
            sections.append(section)
    captions = []
    for fig in body.findall("tei:figure", NS) if body is not None else []:
        label = text(fig.find("tei:head", NS)) or text(fig.find("tei:label", NS))
        desc = text(fig.find("tei:figDesc", NS))
        if desc:
            captions.append(f"{label}: {desc}" if label and not desc.startswith(label) else desc)
    references = [reference(b) for b in root.findall(".//tei:back//tei:listBibl/tei:biblStruct", NS)]
    return FullText(
        title=text(header.find(".//tei:titleStmt/tei:title", NS)) if header is not None else "",
        authors=authors,
        abstract=abstract,
        sections=sections,
        captions=captions,
        references=[r for r in references if r],
        parser=parser,
        doi=text(doi) or None,
    )


def reference(b: ET.Element) -> str:
    names = [person(a) for a in b.findall(".//tei:author", NS)]
    names = [n for n in names if n]
    title = text(b.find("tei:analytic/tei:title", NS)) or text(b.find("tei:monogr/tei:title", NS))
    venue = text(b.find("tei:monogr/tei:title", NS)) if b.find("tei:analytic", NS) is not None else ""
    date = b.find(".//tei:imprint/tei:date", NS)
    year = (date.get("when") or text(date))[:4] if date is not None else ""
    doi = text(b.find(".//tei:idno[@type='DOI']", NS))
    who = ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")
    parts = [who, year, title, venue, f"doi:{doi}" if doi else ""]
    return ". ".join(p.rstrip(".") for p in parts if p) + ("." if any(parts) else "")


def markdown(full: FullText, record: Paper | None = None) -> str:
    """The paper as Markdown: the record's header when there is one (its authors, venue and
    ids are cleaner than a parsed title page), then GROBID's abstract and body."""
    title = (record.title if record else "") or full.title
    if record:
        header = record.ingest_arguments()["content"].split("\n\n")[1]
    else:
        names = ", ".join(a.name for a in full.authors) or "Unknown authors"
        header = names + (f". doi:{full.doi}" if full.doi else "")
    abstract = full.abstract or (record.abstract if record else "") or "(no abstract)"
    out = [f"# {title}", header, "## Abstract", abstract]
    for s in full.sections:
        out.append(f"{'#' * s.level} {s.heading}")
        out += s.paragraphs
    if full.captions:
        out.append("## Figures and tables")
        out += full.captions
    if full.references:
        out.append("## References")
        out.append("\n".join(f"- {r}" for r in full.references))
    return "\n\n".join(out) + "\n"


def ingest_arguments(full: FullText, record: Paper | None, pdf_url: str) -> dict[str, Any]:
    """ingest_source arguments for the full text. With the paper's record, the collection is
    the same as its abstract's (doi:... or arxiv:...), so the full text is a newer version of
    the same source: a run over it replaces what the abstract's run found."""
    args = record.ingest_arguments() if record else {"media_type": "text/markdown", "title": full.title}
    args["content"] = markdown(full, record)
    args["metadata"] = {**args.get("metadata", {}), "parser": full.parser, "pdf_url": pdf_url}
    args.setdefault("uri", pdf_url)
    if not record:
        origins = list(dict.fromkeys(o for a in full.authors if (o := a.origin())))[:64]
        if origins:
            args["origins"] = origins
    return args


def fetch_pdf(url: str, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "wmk-papers (World Model Kernel)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_PDF_BYTES + 1)
    except OSError as exc:
        raise FullTextError(f"could not fetch {url}: {type(exc).__name__}") from exc
    if len(data) > MAX_PDF_BYTES:
        raise FullTextError(f"{url} is larger than {MAX_PDF_BYTES // 1_000_000} MB")
    if not data.startswith(b"%PDF"):
        raise FullTextError(f"{url} did not return a PDF")
    return data


def grobid_tei(base_url: str, pdf: bytes, timeout: float = 300) -> str:
    """POST the PDF to GROBID's processFulltextDocument; the TEI XML it returns."""
    boundary = uuid.uuid4().hex
    fields = {"consolidateHeader": "0", "consolidateCitations": "0"}
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields.items()
    )
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="input"; filename="paper.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode()
    body += pdf + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/processFulltextDocument",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/xml"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return str(response.read().decode("utf-8"))
    except OSError as exc:
        raise FullTextError(f"GROBID could not parse the PDF: {type(exc).__name__}") from exc
