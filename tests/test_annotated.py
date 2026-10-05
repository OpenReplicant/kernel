"""The annotated-corpus scorer (evals/annotated.py), checked without a model: a scripted
extraction and judgement of one item are scored against its gold label and rationale."""

from __future__ import annotations

import psycopg

from evals.annotated import Item, covered, load_corpus, score_item, summarise, wilson
from kernel.testing import KernelDB

ITEM = Item(
    id="t-1",
    hypothesis="Drug X lowers blood pressure.",
    label="SUPPORT",
    rationale=(1,),
    doc_id="1",
    title="A trial of drug X",
    sentences=(
        "We ran a randomised trial of drug X in 200 adults.",
        "Drug X lowered systolic blood pressure by 9 mmHg against placebo.",
        "Side effects were mild.",
    ),
    uri="https://example.org/1",
    collection="s2:1",
)


def test_rendered_spans_are_the_sentences() -> None:
    content, spans = ITEM.render()
    assert [content[a:b] for a, b in spans] == list(ITEM.sentences)


def test_a_quote_covers_a_sentence_only_by_most_of_it() -> None:
    spans = [(0, 100), (101, 200)]
    assert covered([(10, 60)], spans) == {0}
    assert covered([(80, 190)], spans) == {1}  # 20 characters into the first sentence do not count
    assert covered([(0, 200)], spans) == {0, 1}
    assert wilson(0, 0) == (0.0, 1.0) and wilson(10, 10)[1] == 1.0


def test_the_shipped_sample_is_balanced_and_well_formed() -> None:
    items = load_corpus("scifact")
    assert len(items) == 30 and {i.label for i in items} == {"SUPPORT", "CONTRADICT", "NEI"}
    assert len({i.doc_id for i in items}) == 30
    for item in items:
        assert all(0 <= r < len(item.sentences) for r in item.rationale)
        assert bool(item.rationale) == (item.label != "NEI")


def test_scoring_a_scripted_item(kdb: KernelDB, agent: str) -> None:
    content, _ = ITEM.render()
    source = kdb.ingest({"content": content, "media_type": "text/markdown", **ITEM.ingest_args()}, agent)
    chunk = source["chunks"][-1]["id"]

    def finding(text: str, quote: str) -> str:
        result = kdb.claim(agent, text, [{"op": "promote", "ref": "$f"}], source=chunk, quote=quote)
        return str(result["refs"]["$f"])

    design = finding("The trial enrolled 200 adults.", "randomised trial of drug X in 200 adults")
    effect = finding(
        "Drug X lowered systolic pressure by 9 mmHg.", "lowered systolic blood pressure by 9 mmHg"
    )
    hypothesis = kdb.claim(
        agent, ITEM.hypothesis, [{"op": "promote", "ref": "$h"}], basis="inferred", modality="hypothetical"
    )["refs"]["$h"]
    kdb.claim(
        agent,
        "The trial's result supports the hypothesis.",
        [{"op": "assert", "edge": "supports", "from": effect, "to": hypothesis}],
        basis="inferred",
        confidence="medium",
    )
    with psycopg.connect(kdb.dsn) as conn:
        scored = score_item(conn, ITEM, source["source_id"], hypothesis)
    assert design != effect
    assert (scored.predicted, scored.findings, scored.captured, scored.related_sentences) == (
        "SUPPORT",
        2,
        [1],
        [1],
    )
    assert [(r["edge"], r["confidence"], r["correct"]) for r in scored.relations] == [
        ("supports", "medium", True)
    ]
    summary = summarise([scored])
    assert summary["verdict"]["accuracy"] == 1.0
    assert summary["evidence_capture"]["recall"] == 1.0
    assert summary["rationale"] == {"precision": 1.0, "recall": 1.0}
    assert summary["calibration"] == {"medium": {"relations": 1, "correct": 1, "accuracy": 1.0}}
