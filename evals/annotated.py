"""`make annotated`: extraction measured against data that people other than us annotated.

`make eval` replays scripted extraction, and `make live` scores a model against graphs we
wrote ourselves: both show the plumbing works, neither shows how well a model extracts.
This harness measures the model on an independently annotated corpus.

Corpus: a fixed sample of SciFact (Wadden et al., EMNLP 2020; claims CC BY 4.0, abstracts
ODC-By 1.0, see evals/annotated/scifact/NOTICE.md). Each item pairs a scientific claim with
one abstract that experts labelled SUPPORT, CONTRADICT or NEI (not enough information), and
for the first two named the abstract's rationale sentences.

Protocol, per item, each phase a fresh session of a real harness (headless Claude Code, as
in `make live`) connected to a running gateway on the eval profile:

  A. Extraction, blind. The model maps the abstract with the research skill: one run, one
     finding per result, each quoting its sentence. It never sees the claim.
  B. Judgement from the graph. The claim is recorded as a synthesis hypothesis; the model
     relates the paper's findings to it with `supports` or `contradicts`, or relates none,
     reading the findings from the world model, not the abstract.

Measures, in evals/out/annotated.json:

  - evidence capture: the share of the annotators' rationale sentences that phase A's
    findings quote (recall of extraction on the sentences that decide the claim);
  - verdict: phase B's label per item (MIXED when it both supports and contradicts) against
    the gold label, with accuracy, its 95% Wilson interval, macro F1 and a confusion matrix;
  - rationale precision and recall: the sentences quoted by the findings phase B related,
    against the rationale sentences;
  - calibration: how often phase B's relations were right, by the confidence the model
    gave them.

Bring your own corpus as JSON lines in the same format (`Item`); `--corpus` takes a path.
Each run costs model usage (roughly US$0.30 to 0.60 per item with a mid-sized model), so
it is not part of CI; `score` re-scores a database without calling a model.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import shutil
import sys
import tarfile
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import psycopg
from mcp import Client

from evals.live import turn, workspace
from evals.replay import replay
from kernel import admin

ROOT = Path(__file__).resolve().parent.parent
CORPORA = ROOT / "evals" / "annotated"
REPORT = ROOT / "evals" / "out" / "annotated.json"
MANIFEST = ROOT / "evals" / "out" / "annotated-run.json"

SCIFACT_URL = "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz"
SCIFACT_SHA256 = "11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be"
LABELS = ("SUPPORT", "CONTRADICT", "NEI")

NOTES = """You are mapping papers into a World Model Kernel for a research field map. Use the
world-model-core skill and the research skill for every paper. Papers, methods, data and
measures go in the `research` namespace. The wmk MCP server's instructions name your agent
id. Keep replies short.
"""
EXTRACT_PROMPT = """Map this paper into the world model with the research skill. Ingest it
with exactly these ingest_source arguments and the content below as `content`: {ingest}

{content}"""
JUDGE_PROMPT = """The world model holds a synthesis hypothesis, Claim node {hypothesis}:
"{text}"

Judge it against one paper only: "{title}" (source collection {collection}), using what the
world model holds about that paper. Read the paper's findings and their quotes with the
kernel's tools; do not rely on memory. Relate each of its findings that bears on the
hypothesis to {hypothesis} with `supports` or `contradicts`, as the research skill says for
relations you draw yourself. If none of its findings bears on the hypothesis, relate nothing.
End your reply with one word: SUPPORT, CONTRADICT or NONE."""


# Corpus -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One annotated pair: a hypothesis, a document, the gold label and the gold rationale
    (indices into the document's sentences; empty for NEI)."""

    id: str
    hypothesis: str
    label: str
    rationale: tuple[int, ...]
    doc_id: str
    title: str
    sentences: tuple[str, ...]
    uri: str
    collection: str

    @classmethod
    def from_json(cls, row: dict[str, Any]) -> Item:
        doc = row["document"]
        if row["label"] not in LABELS:
            raise ValueError(f"{row['id']}: label must be one of {LABELS}")
        return cls(
            id=row["id"],
            hypothesis=row["hypothesis"],
            label=row["label"],
            rationale=tuple(row.get("rationale", [])),
            doc_id=str(doc["id"]),
            title=doc["title"],
            sentences=tuple(doc["sentences"]),
            uri=doc["uri"],
            collection=doc["collection"],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "hypothesis": self.hypothesis,
            "label": self.label,
            "rationale": list(self.rationale),
            "document": {
                "id": self.doc_id,
                "title": self.title,
                "sentences": list(self.sentences),
                "uri": self.uri,
                "collection": self.collection,
            },
        }

    def render(self) -> tuple[str, list[tuple[int, int]]]:
        """The document as the source's Markdown content, and each sentence's character span
        [start, end) in it: the offsets kernel quotes are recorded in."""
        head = f"# {self.title}\n\n{self.uri}\n\n## Abstract\n\n"
        spans: list[tuple[int, int]] = []
        body = ""
        for sentence in self.sentences:
            if body:
                body += " "
            spans.append((len(head) + len(body), len(head) + len(body) + len(sentence)))
            body += sentence
        return head + body + "\n", spans

    def ingest_args(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "media_type": "text/markdown",
            "uri": self.uri,
            "collection": self.collection,
            "metadata": {"corpus_id": self.doc_id},
        }


def load_corpus(name_or_path: str) -> list[Item]:
    path = Path(name_or_path)
    if not path.exists():
        path = CORPORA / name_or_path / "sample.jsonl"
    return [Item.from_json(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def scifact_release(cache: Path) -> tarfile.TarFile:
    """The pinned SciFact release, downloaded once into `cache` and checked against its hash."""
    archive = cache / "scifact-data.tar.gz"
    if not archive.exists():
        cache.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(SCIFACT_URL, timeout=120) as response:
            archive.write_bytes(response.read())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != SCIFACT_SHA256:
        raise SystemExit(f"{archive}: sha256 {digest} is not the pinned release {SCIFACT_SHA256}")
    return tarfile.open(archive)


def scifact_sample(per_label: int, cache: Path) -> list[Item]:
    """`per_label` items for each label from SciFact's dev split, one per abstract, in a fixed
    order (by the hash of claim and document ids) so the sample never depends on chance."""

    def jsonl(tar: tarfile.TarFile, name: str) -> list[dict[str, Any]]:
        member = tar.extractfile(name)
        assert member is not None
        return [json.loads(line) for line in io.TextIOWrapper(member, encoding="utf-8") if line.strip()]

    with scifact_release(cache) as tar:
        corpus = {str(d["doc_id"]): d for d in jsonl(tar, "data/corpus.jsonl")}
        claims = jsonl(tar, "data/claims_dev.jsonl")
    candidates: list[tuple[str, dict[str, Any], str, str, set[int]]] = []
    for claim in claims:
        if not claim["evidence"]:
            for doc in claim["cited_doc_ids"]:
                candidates.append((f"{claim['id']}:{doc}", claim, str(doc), "NEI", set()))
        for doc, sets in claim["evidence"].items():
            labels = {s["label"] for s in sets}
            if len(labels) == 1:
                label = labels.pop()
                sentences = {i for s in sets for i in s["sentences"]}
                candidates.append((f"{claim['id']}:{doc}", claim, str(doc), label, sentences))
    candidates.sort(key=lambda c: hashlib.sha256(c[0].encode()).hexdigest())
    chosen: list[Item] = []
    used: set[str] = set()
    taken: Counter[str] = Counter()
    for key, claim, doc, label, sentences in candidates:
        if taken[label] >= per_label or doc in used or doc not in corpus:
            continue
        used.add(doc)
        taken[label] += 1
        abstract = corpus[doc]
        chosen.append(
            Item(
                id=f"scifact-dev-{key.replace(':', '-')}",
                hypothesis=claim["claim"],
                label=label,
                rationale=tuple(sorted(sentences)),
                doc_id=doc,
                title=abstract["title"],
                sentences=tuple(abstract["abstract"]),
                uri=f"https://api.semanticscholar.org/CorpusID:{doc}",
                collection=f"s2:{doc}",
            )
        )
    return sorted(chosen, key=lambda i: (LABELS.index(i.label), i.id))


# Scoring ------------------------------------------------------------------------------------


def covered(quotes: list[tuple[int, int]], spans: list[tuple[int, int]]) -> set[int]:
    """Sentences a quote covers: the overlap is at least half of the shorter of the two, so a
    quote spilling a few words into the next sentence does not count for it."""
    hit: set[int] = set()
    for i, (s0, s1) in enumerate(spans):
        for q0, q1 in quotes:
            overlap = min(s1, q1) - max(s0, q0)
            if overlap > 0 and overlap * 2 >= min(s1 - s0, q1 - q0):
                hit.add(i)
    return hit


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 1.0)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3))


@dataclass
class ItemScore:
    id: str
    gold: str
    predicted: str
    findings: int
    rationale: list[int]
    captured: list[int]
    related_sentences: list[int]
    relations: list[dict[str, Any]] = field(default_factory=list)
    elsewhere: int = 0


def score_item(
    conn: psycopg.Connection[Any], item: Item, source_id: str | None, hypothesis: str | None
) -> ItemScore:
    _, spans = item.render()
    findings: dict[str, tuple[int, int] | None] = {}
    if source_id:
        # A finding: a Claim node promoted by a claim citing this paper, not rejected since.
        for node_id, q0, q1 in conn.execute(
            "SELECT n.id, c.quote_start, c.quote_end FROM kernel.nodes n "
            "JOIN kernel.claims c ON c.id = n.claim_id "
            "WHERE n.type = 'Claim' AND c.source_id = %s AND n.belief_status <> 'rejected'",
            [source_id],
        ):
            findings[node_id] = (q0, q1) if q0 is not None else None
    relations: list[dict[str, Any]] = []
    elsewhere = 0
    if hypothesis:
        for edge_id, edge, from_id, confidence in conn.execute(
            "SELECT e.id, e.edge, e.from_id, (SELECT a.confidence FROM kernel.assertions a "
            "  WHERE a.target_type = 'edge' AND a.target_id = e.id AND a.polarity = 1 "
            "  ORDER BY a.log_offset DESC, a.op_index DESC LIMIT 1) "
            "FROM kernel.edges e WHERE e.to_id = %s AND e.edge IN ('supports', 'contradicts') "
            "AND e.belief_status <> 'rejected' ORDER BY e.id",
            [hypothesis],
        ):
            if from_id in findings:
                relations.append(
                    {"edge_id": edge_id, "edge": edge, "finding": from_id, "confidence": confidence}
                )
            else:
                elsewhere += 1
    kinds = {r["edge"] for r in relations}
    predicted = (
        "MIXED"
        if kinds == {"supports", "contradicts"}
        else "SUPPORT"
        if kinds == {"supports"}
        else "CONTRADICT"
        if kinds == {"contradicts"}
        else "NEI"
    )
    for r in relations:
        r["correct"] = (r["edge"], item.label) in {("supports", "SUPPORT"), ("contradicts", "CONTRADICT")}
    all_quotes = [q for q in findings.values() if q]
    related_quotes = [q for r in relations if (q := findings[r["finding"]])]
    return ItemScore(
        id=item.id,
        gold=item.label,
        predicted=predicted,
        findings=len(findings),
        rationale=list(item.rationale),
        captured=sorted(covered(all_quotes, spans) & set(item.rationale)),
        related_sentences=sorted(covered(related_quotes, spans)),
        relations=relations,
        elsewhere=elsewhere,
    )


def summarise(scores: list[ItemScore]) -> dict[str, Any]:
    n = len(scores)
    right = sum(s.predicted == s.gold for s in scores)
    confusion = Counter((s.gold, s.predicted) for s in scores)
    f1s = []
    for label in LABELS:
        tp = confusion[(label, label)]
        fp = sum(v for (g, p), v in confusion.items() if p == label and g != label)
        fn = sum(v for (g, p), v in confusion.items() if g == label and p != label)
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 1.0)
    gold_sentences = sum(len(s.rationale) for s in scores)
    captured = sum(len(s.captured) for s in scores)
    with_rationale = [s for s in scores if s.rationale]
    related = sum(len(s.related_sentences) for s in with_rationale)
    related_right = sum(len(set(s.related_sentences) & set(s.rationale)) for s in with_rationale)
    bands: dict[str, dict[str, int]] = {}
    for s in scores:
        for r in s.relations:
            band = bands.setdefault(r["confidence"] or "unknown", {"relations": 0, "correct": 0})
            band["relations"] += 1
            band["correct"] += int(r["correct"])
    for band in bands.values():
        band["accuracy"] = round(band["correct"] / band["relations"], 3)  # type: ignore[assignment]
    return {
        "items": n,
        "verdict": {
            "accuracy": round(right / n, 3) if n else None,
            "accuracy_95ci": wilson(right, n),
            "macro_f1": round(sum(f1s) / len(f1s), 3),
            "f1": {label: round(f, 3) for label, f in zip(LABELS, f1s, strict=True)},
            "confusion": {f"{g}->{p}": v for (g, p), v in sorted(confusion.items())},
        },
        "evidence_capture": {
            "rationale_sentences": gold_sentences,
            "captured": captured,
            "recall": round(captured / gold_sentences, 3) if gold_sentences else None,
            "recall_95ci": wilson(captured, gold_sentences),
        },
        "rationale": {
            "precision": round(related_right / related, 3) if related else None,
            "recall": round(related_right / gold_sentences, 3) if gold_sentences else None,
        },
        "findings_per_abstract": round(sum(s.findings for s in scores) / n, 2) if n else None,
        "relations_to_other_papers": sum(s.elsewhere for s in scores),
        "calibration": dict(sorted(bands.items())),
    }


def score(dsn: str, items: list[Item], manifest: dict[str, Any]) -> dict[str, Any]:
    """Scores for the items whose phases all ran; an item a harness error cut short (a session
    limit, a crash) is listed as excluded, never scored as if the model had judged it."""
    failed = sorted(i.id for i in items if any(p["error"] for p in manifest.get(i.id, {}).get("phases", [])))
    with psycopg.connect(dsn) as conn:
        scores = []
        for item in items:
            if item.id in failed:
                continue
            entry = manifest.get(item.id, {})
            source_id = entry.get("source_id")
            if source_id is None:
                row = conn.execute(
                    "SELECT id FROM kernel.sources WHERE collection = %s ORDER BY recorded_at DESC LIMIT 1",
                    [item.collection],
                ).fetchone()
                source_id = row[0] if row else None
            scores.append(score_item(conn, item, source_id, entry.get("hypothesis")))
    return {"summary": summarise(scores), "excluded": failed, "items": [s.__dict__ for s in scores]}


# Running ------------------------------------------------------------------------------------


async def record_hypothesis(url: str, text: str) -> str:
    """The claim as a synthesis hypothesis, written through the gateway as the eval agent."""
    async with Client(url) as client:
        head = await client.call_tool("query_log", {"limit": 1})
        offset = int(json.loads(head.content[0].text)["head_offset"])  # type: ignore[union-attr]
        result = await client.call_tool(
            "write",
            {
                "claim": {"text": text, "basis": "inferred", "modality": "hypothetical", "confidence": "low"},
                "read_at_offset": offset,
                "ops": [{"op": "promote", "ref": "$h"}],
            },
        )
        content = json.loads(result.content[0].text)  # type: ignore[union-attr]
        if result.is_error or "refs" not in content:
            raise RuntimeError(f"could not record the hypothesis: {content}")
        return str(content["refs"]["$h"])


def source_for(dsn: str, collection: str) -> str | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT id FROM kernel.sources WHERE collection = %s ORDER BY recorded_at DESC LIMIT 1",
            [collection],
        ).fetchone()
    return row[0] if row else None


def run(args: argparse.Namespace) -> int:
    if not shutil.which("claude"):
        sys.exit("the claude CLI is required for a live run")
    items = load_corpus(args.corpus)
    if args.only:
        items = [i for i in items if i.id in set(args.only)]
    if args.limit:
        items = items[: args.limit]
    dsn = admin.dsn_for(admin.admin_dsn(), args.database)
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT count(*) FROM kernel.nodes WHERE type <> 'Agent'").fetchone()
        profiles = {
            r[0]
            for r in conn.execute(
                "SELECT identity ->> 'profile' FROM kernel.nodes "
                "WHERE type = 'Agent' AND identity ? 'profile'"
            )
        }
    if row and row[0] and not args.allow_existing:
        sys.exit(
            f"{args.database} already holds {row[0]} nodes; start from a fresh stack or pass --allow-existing"
        )
    if profiles != {"eval"}:
        sys.exit(f"needs a gateway on the eval profile (found {sorted(profiles)}): WMK_PROFILE=eval make up")
    cwd = workspace(
        args.url, {"session": "annotated", "notes": NOTES, "skills": ["skills/core", "packs/research"]}
    )
    manifest: dict[str, Any] = (
        json.loads(MANIFEST.read_text()) if args.allow_existing and MANIFEST.exists() else {}
    )
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    for n, item in enumerate(items, start=1):
        if manifest.get(item.id, {}).get("done"):
            continue
        entry: dict[str, Any] = {"phases": []}
        content, _ = item.render()
        prompts = [
            EXTRACT_PROMPT.format(ingest=json.dumps(item.ingest_args(), ensure_ascii=False), content=content)
        ]
        for phase, prompt in (("extract", prompts[0]), ("judge", None)):
            if phase == "judge":
                entry["source_id"] = source_for(dsn, item.collection)
                entry["hypothesis"] = anyio.run(record_hypothesis, args.url, item.hypothesis)
                prompt = JUDGE_PROMPT.format(
                    hypothesis=entry["hypothesis"],
                    text=item.hypothesis,
                    title=item.title,
                    collection=item.collection,
                )
            started = time.time()
            try:
                result = turn(cwd, prompt or "", None, args.max_steps, model=args.model)
            except (RuntimeError, json.JSONDecodeError) as exc:
                result = {"is_error": True, "result": str(exc)[-500:]}
            entry["phases"].append(
                {
                    "phase": phase,
                    "steps": result.get("num_turns"),
                    "cost_usd": result.get("total_cost_usd"),
                    "seconds": round(time.time() - started),
                    "error": bool(result.get("is_error")),
                    "reply": (result.get("result") or "")[-400:],
                }
            )
        # An item a harness error cut short is run again by the next run with --allow-existing.
        entry["done"] = not any(p["error"] for p in entry["phases"])
        manifest[item.id] = entry
        MANIFEST.write_text(json.dumps(manifest, indent=2))
        cost = sum(p["cost_usd"] or 0 for p in entry["phases"])
        seconds = sum(p["seconds"] for p in entry["phases"])
        print(f"[{n}/{len(items)}] {item.id} ({item.label}): ${cost:.2f}, {seconds}s")
    return report(dsn, args, items, manifest)


def report(dsn: str, args: argparse.Namespace, items: list[Item], manifest: dict[str, Any]) -> int:
    scored = score(dsn, items, manifest)
    phases = [p for e in manifest.values() for p in e.get("phases", [])]
    # The replay rebuilds with this checkout's kernel SQL: skip it when the stack runs another.
    entries, diff = replay(args.database) if args.replay else (0, [])
    scored["run"] = {
        "model": args.model,
        "cost_usd": round(sum(p["cost_usd"] or 0 for p in phases), 2),
        "seconds": sum(p["seconds"] for p in phases),
        "errors": sum(p["error"] for p in phases),
        "replayed_entries": entries if args.replay else None,
        "replay_exact": not diff if args.replay else None,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(scored, indent=2))
    s = scored["summary"]
    v, ev, ra = s["verdict"], s["evidence_capture"], s["rationale"]
    print(f"{s['items']} items; findings per abstract {s['findings_per_abstract']}")
    if scored["excluded"]:
        print(f"excluded after harness errors (run again with --allow-existing): {scored['excluded']}")
    print(f"verdict accuracy {v['accuracy']} (95% CI {v['accuracy_95ci']}), macro F1 {v['macro_f1']}")
    print(f"  confusion {v['confusion']}")
    print(
        f"evidence capture {ev['recall']} of {ev['rationale_sentences']} rationale sentences "
        f"(95% CI {ev['recall_95ci']})"
    )
    print(f"rationale of related findings: precision {ra['precision']}, recall {ra['recall']}")
    print(f"calibration {s['calibration']}")
    if args.replay:
        print(f"replay: {entries} entries, {'graph reproduced exactly' if not diff else 'DIFF'}")
    print(f"report: {REPORT.relative_to(ROOT)}")
    return 0 if not diff else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure extraction against an independently annotated corpus."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sample = sub.add_parser("sample", help="rebuild the SciFact sample from the pinned release")
    sample.add_argument("--per-label", type=int, default=10)
    sample.add_argument("--cache", type=Path, default=ROOT / "evals" / "out" / "cache")
    for name in ("run", "score"):
        p = sub.add_parser(
            name, help="map and judge each item with a live model" if name == "run" else "re-score"
        )
        p.add_argument(
            "--corpus", default="scifact", help="a corpus under evals/annotated, or a JSON lines path"
        )
        p.add_argument("--url", default="http://localhost:8000/mcp")
        p.add_argument("--database", default="wmk")
        p.add_argument("--model", default=None, help="the harness model (default: the CLI's)")
        p.add_argument("--limit", type=int, default=0)
        p.add_argument("--only", nargs="*", default=[])
        p.add_argument("--max-steps", type=int, default=60)
        p.add_argument(
            "--allow-existing", action="store_true", help="continue a run in a database that has data"
        )
        p.add_argument("--no-replay", dest="replay", action="store_false", help="skip the replay check")
    args = parser.parse_args()
    if args.command == "sample":
        items = scifact_sample(args.per_label, args.cache)
        out = CORPORA / "scifact" / "sample.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(i.to_json(), ensure_ascii=False) + "\n" for i in items))
        print(f"{len(items)} items -> {out.relative_to(ROOT)} ({Counter(i.label for i in items)})")
        return
    if args.command == "score":
        manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
        # The items the last run reached (or those named), not the whole corpus.
        wanted = set(args.only) or {k for k, v in manifest.items() if v.get("done")}
        items = [i for i in load_corpus(args.corpus) if i.id in wanted]
        sys.exit(report(admin.dsn_for(admin.admin_dsn(), args.database), args, items, manifest))
    sys.exit(run(args))


if __name__ == "__main__":
    main()
