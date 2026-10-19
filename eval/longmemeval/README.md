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
OPENROUTER_API_KEY=... KK_ADMIN_URL=... python eval/longmemeval/run.py answer --n 100 --label lfm-k5
python eval/longmemeval/run.py judge-prompts --label lfm-k5 > prompts.jsonl   # judged by Haiku sub-agents
python eval/longmemeval/run.py score --label lfm-k5 --judgments verdicts.jsonl
```

Models follow the owner's rule (CLAUDE.md): a reader under 10B parameters on OpenRouter's free tier, and Haiku
as harness sub-agents for judging.

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

## End to end (Oct 2026, the same 100 questions plus abstention: 101)

The kernel recalls (BM25 with re-ranking) and the top 5 sessions go, in date order, to
`liquid/lfm-2.5-2.6b:free` (2.6B parameters) with the paper's reader prompt. Haiku judges each answer
with the paper's judge prompt for its question type. Five verdicts were checked by hand, and all five
were right. Run: `runs/2026-10-19-lfm-k5-qa.json`.

| Type | n | accuracy |
| --- | --- | --- |
| single-session-assistant | 11 | 1.00 |
| single-session-user | 12 | 0.92 |
| abstention | 8 | 0.75 |
| temporal-reasoning | 26 | 0.62 |
| knowledge-update | 13 | 0.54 |
| single-session-preference | 6 | 0.50 |
| multi-session | 25 | 0.44 |
| **all** | 101 | **0.644** |

- **Context.** On the full 500 questions with GPT-4o judging, the paper and Zep report 0.60 for GPT-4o
  reading the whole history, and 0.71 for Zep with GPT-4o. Ours is a 2.6B reader on a 100-question subset
  with a different judge, so the numbers are not a ranking. They do say that a small model with the
  kernel's recall is in the same range as a large model given everything.
- **3 answers were empty.** The reasoning model ran out of tokens; they count as wrong.
- **Where it loses: multi-session (0.44) and knowledge update (0.54).** These need several sessions
  combined, or the latest value chosen over an earlier one. That is the ground for conclusions with
  premises and supersede (D-90), and for M6-T13's time-aware recall. Five sessions also leave little room
  for counting questions.
