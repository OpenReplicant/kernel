# Benchmarks to compete on (Oct 2026)

Where the kernel can be compared with **published** numbers on the same protocol, rather than by
reproducing other systems. Surveyed 2026-10-17 from the sources linked; vendor numbers are marked
self-reported. Our results live in `eval/*/runs/` and the decisions that cite them (D-59–D-63).

## Recall: BEIR SciFact, nDCG@10 (no model; like for like)

| System | nDCG@10 | Source |
| --- | --- | --- |
| BM25 (multifield / flat) | 0.665 / 0.679 | BEIR paper; [Pyserini 2CR](https://castorini.github.io/pyserini/2cr/beir.html) |
| ColBERTv2 | 0.693 | [arXiv 2112.01488](https://arxiv.org/abs/2112.01488), Table 5 |
| SPLADE++ ED | 0.704 | Pyserini |
| bge-small-en-v1.5 | 0.713 | model card (MTEB) |
| e5-base-v2 | 0.719 | model card (MTEB) |
| bge-base-en-v1.5 | 0.740 / 0.741 | model card; Pyserini |
| tuned dense + sparse fusion (BM42) | 0.753 | [arXiv 2609.22770](https://arxiv.org/abs/2609.22770) |
| gte-base | 0.762 | model card (MTEB) |
| **the kernel: BM25 (D-60)** | **0.659** | `eval/scifact/runs/` |
| **the kernel: BM25 + bge-small (D-61)** | **0.723** | `eval/scifact/runs/` |

Our numbers rank claims (a paper's title and abstract are separate claims), not whole documents; our
bge-small alone scored 0.706 offline against the card's 0.713. Next targets: bge-base (0.741), then 0.753.

## Verification: SciFact open, dev split, abstract-level F1 (fine-tuned systems vs an LLM over the kernel)

Retrieval over all 5,183 abstracts, then labels and rationale sentences. The test split's labels were
never released and its leaderboard no longer resolves, so the dev split is the comparable one.
Official protocol: a predicted abstract counts when its label matches; with rationale, when also one
gold rationale set is within the first three predicted sentences.

| System | Label only | Label + rationale | Source |
| --- | --- | --- | --- |
| VeriSci | 51.0 | 48.5 | [arXiv 2004.14974](https://arxiv.org/abs/2004.14974) |
| ParagraphJoint | 65.1 | 59.9 | [ARSJoint, EMNLP 2021](https://aclanthology.org/2021.emnlp-main.290) |
| VerT5erini (BM25) | 66.0 | 62.4 | ARSJoint paper |
| ARSJoint (BioBERT) | 66.7 | 62.4 | ARSJoint paper |
| MultiVerS (test split, context only) | 72.5 | – | [arXiv 2112.01640](https://arxiv.org/abs/2112.01640) |
| the kernel (hybrid recall, top 3) + Haiku, zero-shot, all 300 dev claims | 67.2 | 59.5 | `eval/scifact/runs/` (D-63) |
| **… + one verdict per claim, strict prompt (chosen on train)** | **70.1** | 58.8 | D-64 |
| … + one verdict per claim, original prompt | 69.7 | 62.0 | D-64 |

Level with the best fine-tuned systems on labels (precision 0.61, recall 0.75: the model labels abstracts
SciFact counts as no evidence); behind on rationales. Claim accuracy 0.88 with the kernel, 0.77 without.

## Agent memory: LongMemEval-S (500 questions, ~115k-token histories)

Judge `gpt-4o-2024-08-06` with the paper's prompts. Comparable only with the same answer model.

| System | Answer model | Overall | Knowledge update | Temporal |
| --- | --- | --- | --- | --- |
| full context | gpt-4o-mini | 55.4 | 76.9 | 36.5 |
| Zep | gpt-4o-mini | 63.8 | 74.4 | 54.1 |
| full context | gpt-4o | 60.2 | 78.2 | 45.1 |
| Zep | gpt-4o | 71.2 | 83.3 | 62.4 |
| Supermemory (self-reported) | gpt-4o | 81.6 | 88.5 | 76.7 |
| Mastra observational memory (self-reported) | gpt-4o | 84.2 | 85.9 | 85.7 |

Sources: [arXiv 2410.10813](https://arxiv.org/abs/2410.10813), [Zep arXiv 2501.13956](https://arxiv.org/abs/2501.13956),
[Mastra](https://mastra.ai/research/observational-memory). Embedding ~57M tokens is hours on 4 CPU cores.
LoCoMo is not recommended: published runs differ in judge, prompts and categories, and an audit found
6.4% of its answer key wrong ([Penfield, Apr 2026](https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer)).

## Contradiction and knowledge update (the belief model's ground)

- **MemoryAgentBench** FactConsolidation ([arXiv 2507.05257](https://arxiv.org/abs/2507.05257)), accuracy,
  single-hop / multi-hop: GPT-4o long context 60 / 5, HippoRAG-v2 54 / 5, BM25 RAG 48 / 3, Cognee 28 / 3,
  MemGPT 28 / 3, Mem0 18 / 2, Zep 7 / 3.
- **BEAM** ([arXiv 2510.27246](https://arxiv.org/abs/2510.27246), ICLR 2026): contradiction resolution at
  most 0.05 for every method at 1M tokens; the 128K size is affordable.

## Operations (MAPE-K)

No benchmark runs an LLM-driven MAPE-K loop on docker-compose end to end. By stage:

| Stage | Benchmark | Published to compare with |
| --- | --- | --- |
| Analyse (metrics) | [RCAEval](https://github.com/phamquiluan/RCAEval) RE1, MIT ([arXiv 2412.17015](https://arxiv.org/abs/2412.17015)) | ~15 baselines (BARO, N-Sigma, CIRCA, RCD …), Avg@5. **The kernel's 60 s windows: 0.817 mean, against BARO 0.761 and N-Sigma 0.837** (`eval/rcaeval/`, D-63) |
| Analyse (logs, traces), knowledge value | RCAEval RE2/RE3; [ITBench-AA](https://huggingface.co/datasets/ArtificialAnalysis/ITBench-AA) (CC BY 4.0) | ITBench-AA: Gemma 4 31B 37%, Opus 4.7 47% |
| Plan | [SWIM](https://github.com/cps-sei/swim) (SEAMS 2018) | an LLM manager at ~71% of the reactive baseline's utility ([arXiv 2404.09866](https://arxiv.org/abs/2404.09866)) |
| Full loop | [SREGym](https://github.com/SREGym/SREGym) (MIT, NeurIPS 2026), [AIOpsLab](https://github.com/microsoft/AIOpsLab) (MIT) | AIOpsLab ReAct: detection 77%, RCA 45%, mitigation 36% |
