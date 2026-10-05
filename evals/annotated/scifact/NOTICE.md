# SciFact sample

`sample.jsonl` holds 30 items from the dev split of SciFact: 10 labelled SUPPORT, 10
CONTRADICT and 10 NEI (not enough information), one item per abstract. Each item pairs one
claim with one abstract, gives the annotators' label and lists the abstract's rationale
sentences. `python -m evals.annotated sample` rebuilds the file from the pinned release
(sha256 `11c62128…76be`); the choice is fixed by hashing claim and document ids, not by
chance.

Source: David Wadden, Shanchuan Lin, Kyle Lo, Lucy Lu Wang, Madeleine van Zuylen, Arman
Cohan and Hannaneh Hajishirzi. 2020. *Fact or Fiction: Verifying Scientific Claims.* EMNLP
2020. https://github.com/allenai/scifact

Licences, as stated by the dataset:

- Claims and evidence annotations: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Abstracts: part of the Semantic Scholar S2ORC dataset,
  [ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/).

The items keep the annotators' text exactly, typing errors included. The `id` of each item
is `scifact-dev-<claim id>-<document id>`; `uri` points at the abstract's Semantic Scholar
record.
