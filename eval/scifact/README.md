# SciFact on the kernel (M3-T3, D-59)

**Question.** Is the kernel a good base for a scientific-belief world model: papers ingested
automatically, what they say about a claim recorded with the sentences it rests on, and belief that
follows the evidence? This eval is a first, light check for gaps, not a leaderboard entry.

**Data.** [SciFact](https://github.com/allenai/scifact) (Wadden et al. 2020): 5,183 abstracts and 300
labelled dev claims. Each claim is supported or contradicted by sentences of one or more abstracts, or
has no evidence. BEIR's SciFact test split is the same 300 claims, so retrieval is comparable with
BEIR's numbers. The license is CC BY-NC 2.0: `run.py fetch` downloads it into a cache (sha256 pinned);
it is never committed or shipped.

**Stages.**

| Stage | What runs | Measures | Needs |
| --- | --- | --- | --- |
| 1 retrieval | the paper encoder puts the abstracts in the kernel; each claim goes through the kernel's recall (the MCP `search` tool's query) | nDCG@10 and recall against BEIR's BM25 (0.665); query latency; ingest time | nothing but the kernel |
| 2 verification | for each claim, the top 3 abstracts the kernel recalls go to a model, which labels each and quotes the sentences; the same claim goes to the model alone (closed book) | SciFact abstract-level F1 (label only, and with rationale); claim accuracy with and without the kernel; how many quotes are found verbatim | a model: any OpenAI-compatible endpoint (OpenRouter, Ollama, llama.cpp), or answers from a harness sub-agent (`--answers`) |
| 3 belief | the stage-2 verdicts recorded as claims (`<paper> cito:supports|cito:refutes <proposition>`, extracted, with the quotes), then the belief model | whether belief in each proposition separates supported from contradicted claims | stage 2 (answers file); the default belief model reads supports and refutes (D-62) |

```sh
python eval/scifact/run.py fetch
DATABASE_URL=<kk_authenticator> python eval/scifact/run.py encode      # with kk serve running (validator, belief)
AGENT_DATABASE_URL=<an agent's login> python eval/scifact/run.py retrieval
AGENT_DATABASE_URL=... OPENROUTER_API_KEY=... python eval/scifact/run.py verify --n 100
AGENT_DATABASE_URL=... python eval/scifact/run.py prompts --n 60 > prompts.jsonl   # for a sub-agent;
python eval/scifact/run.py verify --n 60 --answers answers.jsonl                   # then score its answers
AGENT_DATABASE_URL=... python eval/scifact/run.py belief --n 60 --answers answers.jsonl   # stage 3
```

Runs are kept in `runs/` (results only, no data). `test_score.py` checks the scorer on a fixture,
including that broken answers score worse.

## Retrieval (2026-10-17, all 300 dev claims)

| Recall | nDCG@10 | Recall, top 200 claims | Query p50 |
| --- | --- | --- | --- |
| Postgres full-text search (no index) | 0.296 | | 1,971 ms |
| BM25, recall-index (D-60) | 0.659 | 0.93 | 274 ms |
| BM25 + bge-small-en-v1.5, recall-dense (D-61) | 0.723 | 0.97 | 476 ms |

BEIR's BM25 baseline is 0.665.

## Verification so far (2026-10-17, 60 dev claims, top 3 abstracts from BM25 recall)

| Model | Abstract F1 (label) | F1 (rationale) | Claim accuracy, with kernel | Closed book | Quotes verbatim |
| --- | --- | --- | --- | --- | --- |
| Claude Haiku (sub-agent) | 0.78 | 0.70 | 0.91 | 0.78 | 100% |
| Nemotron 3 Super 120B (A12B, free) | 0.75 | 0.73 | 0.89 | 0.76 | 97.8% |
| Liquid LFM 2.5 2.6B (free) | 0.68 | 0.68 | 0.84 | 0.58 | 97.2% |

With the kernel's recall a 2.6B model comes within 7 points of Haiku's claim accuracy, and gains 27 points
over its own closed-book accuracy. Small models misquote a few sentences (2–3%), which is the case for
the quote verifier (M3-T6). With the earlier full-text search instead of BM25, Haiku scored 0.51: recall,
not the model, was the gap.

## Belief (stage 3, 2026-10-17, Haiku's verdicts on the same 60 claims)

| Supported claims | Contradicted | No evidence | AUC, supported vs contradicted | Sign right | Mean belief (supported / contradicted / none) |
| --- | --- | --- | --- | --- | --- |
| 28 | 17 | 15 | 0.97 | 91% | 0.67 / 0.36 / 0.50 |

Most claims have one evidence abstract, so stage 3 mostly carries stage 2's verdicts through. How belief
accumulates over many works is tested by `eval/belief-v0` and the smoke test (D-62).
