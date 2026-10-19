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

The same reader, `liquid/lfm-2.5-2.6b:free` (2.6B parameters), with the paper's reader prompt, given four
kinds of context:

- **none:** the question alone.
- **full history:** the most recent sessions that fit its window, about 50k tokens of the 115k.
- **kernel:** the top 5 sessions the kernel recalls (BM25 with re-ranking), in date order.
- **kernel, turns (D-92):** the turns the kernel recalls, each with the turn before and after it, grouped
  by session in date order, up to 24k characters (about the size of five sessions): less of each session,
  from more of them (`--mode kernel-turns`).
- **oracle:** only the sessions holding the answer.

Haiku sub-agents judged all 404 answers blind, with the paper's judge prompt for each question type. The
answers were shuffled across the four settings under opaque ids. A second, separate judging of the
kernel's answers agreed on 99 of 101. The turns run was judged later the same way (opaque ids, shuffled).
Runs: `runs/2026-10-19-lfm-{none,full,k5,turns,oracle}-qa.json`.

| Type | n | none | full history | kernel | kernel, turns | oracle |
| --- | --- | --- | --- | --- | --- | --- |
| single-session-assistant | 11 | 0.27 | 0.46 | 0.91 | 0.82 | 1.00 |
| single-session-user | 12 | 0.00 | 0.08 | 0.92 | 1.00 | 1.00 |
| abstention | 8 | 0.88 | 0.75 | 0.75 | 0.88 | 0.88 |
| temporal-reasoning | 26 | 0.00 | 0.15 | 0.61 | 0.65 | 0.58 |
| knowledge-update | 13 | 0.00 | 0.23 | 0.54 | 0.85 | 0.77 |
| single-session-preference | 6 | 0.00 | 0.17 | 0.33 | 0.67 | 1.00 |
| multi-session | 25 | 0.00 | 0.00 | 0.44 | 0.36 | 0.40 |
| **all** | 101 | **0.099** | **0.198** | **0.624** | **0.683** | **0.703** |

- **The kernel closes 87% of the gap** between no memory and the oracle (0.099 to 0.624, against 0.703),
  and 95% with turns and their neighbours instead of whole sessions (0.683). Knowledge update gains most
  (0.54 to 0.85, above the oracle's 0.77): the reader sees the later value from more sessions, without the
  rest of each session to distract it. Multi-session drops two questions (0.44 to 0.36). Kernel recall
  briefs this way (D-92).
- **Giving a small model everything it can hold does little (0.198).** The evidence is often in older
  sessions that do not fit, and a 2.6B model loses the thread in 50k tokens (the paper saw Llama 8B drop
  beyond about 3k tokens of retrieved history). A memory layer matters most under small models.
- **Rough context, not a ranking.** On all 500 questions with GPT-4o judging, the paper and Zep report 0.60
  for GPT-4o reading the whole history, and 0.71 for Zep with GPT-4o.
- **3 answers were empty.** The reasoning model ran out of tokens; they count as wrong.
- **Where the kernel still loses: multi-session (0.44; 0.36 with turns) and, with whole sessions,
  knowledge update (0.54).** Multi-session is low
  even for the oracle (0.40), so part of it is the reader. These need several sessions
  combined, or the latest value chosen over an earlier one. That is the ground for conclusions with
  premises and supersede (D-90), and for M6-T13's time-aware recall. Five sessions also leave little room
  for counting questions.
