# LongMemEval on the kernel (M6-T10)

**Question.** Given a long chat history (LongMemEval_S: about 48 sessions, 115k tokens a question), does
the kernel's memory recall bring up the part of the history that holds the answer?

**Data.** [LongMemEval](https://github.com/xiaowu0162/LongMemEval) (Wu et al., ICLR 2025), the cleaned S
set (MIT). `run.py fetch` downloads it into a cache (sha256 pinned); it is never committed.

**How it runs.**
- Each question's history goes into its own scope, one claim per turn ("<role>: <text>", valid from the
  session's date), through the normal write path: the validator applies it and the recall index indexes it.
- Each question is then asked through the memory recall code (what the MCP `recall` tool runs), as a login
  that can read only that question's scope. Row-level security keeps the histories apart.
- Identifiers are hashed. LongMemEval names its evidence sessions `answer_...`, which must not reach the
  kernel or the ranking.
- It is scored as LongMemEval's own retrieval code does: recall_any, recall_all and NDCG at 5 and 10, by
  turn and by session (turns ranked, then collapsed to sessions). Abstention questions have no evidence and
  are left out.

```sh
python eval/longmemeval/run.py fetch
KK_ADMIN_URL=... python eval/longmemeval/run.py load --n 100        # with kk serve (validator, recall) running
KK_RERANK=0 KK_ADMIN_URL=... python eval/longmemeval/run.py retrieval --n 100 --label bm25
KK_ADMIN_URL=... python eval/longmemeval/run.py retrieval --n 100 --label bm25-rerank
```

## Results (Oct 2026, 100 questions stratified by type, 93 with evidence)

| Recall | session R_any@5 | R_all@5 | NDCG@5 | R_any@10 | turn R_any@5 | turn R_any@10 | preference R_any@5 | p50 latency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BM25 (recall index) | 0.935 | 0.817 | 0.866 | 0.957 | 0.796 | 0.882 | 0.167 | 0.76 s |
| BM25 + cross-encoder re-ranking (D-66) | 0.968 | 0.925 | 0.923 | 1.000 | 0.817 | 0.914 | 0.667 | 7.9 s |

Runs: `runs/2026-10-19-bm25.json`, `runs/2026-10-19-bm25-rerank.json`.

- **Preference questions are where ranking fails.** A question such as "can you suggest a recipe?" shares
  no words with the turn where the preference was stated. Re-ranking lifts it from 0.17 to 0.67, and recall
  cues (D-91) address it at the source.
- **Re-ranking costs latency here.** The cross-encoder reads 60 long chat turns on a 4-core CPU.
- **Dense recall is not measured yet.** Embedding the 50k turns with the recall-dense worker took about 2
  turns a second and 8.6 GB of memory, so it was stopped. A smaller subset, or embedding with truncation,
  comes next.
- **Comparison.** The paper's retrieval tables are on LongMemEval_M (500 sessions a question; BM25
  session R@5 0.634 there). Ours is on S, so the two compare only in direction, not value.
- **Not measured yet.** End-to-end QA accuracy, which needs a reader model and the paper's GPT-4o judge.
