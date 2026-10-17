# Recall: what to adopt (research, Oct 2026)

Research for D-60 (owner's request: recall methods from the literature, with benchmarks and licences, the best taken as modules, options or defaults). Gathered by a research agent on 2026-10-17 from the cited sources; numbers are as each source reports them, and ⚠ marks what could not be verified. Our own measurements are in `eval/scifact/runs/` and D-59/D-60.


The numbers below were read from the cited source (PDF table, model card or vendor page). "Self-reported" means the vendor's own MTEB card metadata. Anything I could not confirm is flagged ⚠.

## 0. Where we are

Our first SciFact run used `ts_rank_cd` over an OR of lexemes and scored nDCG@10 0.296. That function has no IDF and no length normalisation, so most of the gap to BM25 comes from the scoring function, not the index. Reference points on BEIR SciFact (300 claims, 5,183 abstracts) [1]:

| Method | nDCG@10 |
|---|---|
| BM25 | 0.665 |
| docT5query | 0.675 |
| ColBERT | 0.671 |
| TAS-B | 0.643 |
| ANCE | 0.507 |
| DPR | 0.318 |
| BM25 + MiniLM cross-encoder | 0.688 |

## (a) Ranked recommendation

### Adopt as default (permissive licences, all inside Postgres)

1. **BM25 via `pg_textsearch` (Tiger Data). Default recall-index module.**
   - Licence and status: PostgreSQL License. Version 1.0 was the first GA release (blog of 2026-03-31); the latest is 1.5.0. It supports Postgres 17 and 18; Postgres 19 is best-effort [2][3].
   - Interface: a `bm25` index access method (k1=1.2, b=0.75), queried with `ORDER BY col <@> 'q'`.
   - Quality: the vendor published no relevance numbers, only latency. On MS MARCO v2 its weighted p50 is 40.6 ms against ParadeDB's 94.4 ms [3]. We should therefore expect classic BM25 quality: 0.665 in BEIR [1], and 67.89 for Pyserini's BM25 in RankGPT's table [6].
   - Limitations in 1.0 [3]:
     - Matching is OR-only.
     - There are no phrase queries.
     - Each index covers a single column, so a generated column is the workaround.
     - IDF is computed per partition.
     - It requires `shared_preload_libraries`.
   - ⚠ Not verified: how it interacts with RLS. Rows removed by RLS after the index's top-k scan could leave fewer than k results, so the module should over-fetch. ⚠ Per-partition IDF matters if scopes are ever partitioned.
   - Fallback for older Postgres: BM25 in plain SQL over `tsvector` plus a term-statistics table.

2. **Dense embeddings in pgvector, fused with BM25.**
   - pgvector: PostgreSQL License, Postgres 13+. HNSW indexes up to 2,000 dims (`vector`) or 4,000 (`halfvec`) [4].
   - Model candidates, with self-reported SciFact nDCG@10 from their MTEB card metadata [5]:

     | Model | Licence | SciFact nDCG@10 |
     |---|---|---|
     | gte-base-en-v1.5 | Apache-2.0 | 76.77 |
     | mxbai-embed-large-v1 | Apache-2.0 | 74.73 |
     | bge-large-en-v1.5 | MIT | 74.61 |
     | bge-base-en-v1.5 | MIT | 74.04 |
     | e5-large-v2 | MIT | 72.24 |
     | snowflake-arctic-embed-m-v2.0 | Apache-2.0 | 71.8 |
     | bge-small-en-v1.5 | MIT | 71.28 |
     | nomic-embed-text-v1.5 | Apache-2.0 | 70.28 |

   - For multilingual text or long (32K-token) context: Qwen3-Embedding-0.6B (Apache-2.0) scores 61.83 on MTEB English v2 retrieval [5b].
   - Recommendation: bge-base-en-v1.5 or gte-base-en-v1.5 as the default (CPU-friendly), with Qwen3-Embedding-0.6B as an optional upgrade.
   - Fusion: start with RRF (Cormack et al. 2009 [7]), which needs no tuning. Bruch et al. found a tuned convex combination beats RRF in and out of domain, needs few labels, and that RRF is sensitive to its parameter [8]. Expose both.
   - ⚠ I found no published BM25+dense hybrid number for SciFact itself, so we must measure it. Measured (D-61): bge-small + BM25, min-max averaged, 0.730 offline and 0.723 through the kernel, against BM25 0.659.

### Optional modules (ranked by expected gain per cost)

3. **Cross-encoder reranker on the top 50–100.** This is the largest documented SciFact gain.
   - On SciFact, reranking BM25's top 100 [6]:

     | Method | nDCG@10 |
     |---|---|
     | BM25 alone | 67.89 |
     | monoBERT | 71.36 |
     | monoT5-220M | 73.40 |
     | monoT5-3B | 76.57 |
     | GPT-3.5 listwise (RankGPT) | 70.43 |
     | GPT-4 listwise (RankGPT) | 74.95 |

   - On SciFact abstract retrieval (dev), BM25 → monoT5 raised R@3 from 79.90 to 86.12 [9].
   - Cheap open models:

     | Model | Licence | Size | Reported score |
     |---|---|---|---|
     | Qwen3-Reranker-0.6B | Apache-2.0 | 0.6B | MTEB-R 65.80 vs bge-reranker-v2-m3 57.03 (top 100 from Qwen3-Embedding-0.6B) [10] |
     | mxbai-rerank-base-v2 | Apache-2.0 | 0.5B | BEIR 55.57 [11] |
     | bge-reranker-v2-m3 | Apache-2.0 | — | [5] |
     | ms-marco-MiniLM-L6-v2 | Apache-2.0 | 22.7M | TREC DL19 74.30 [12] |

   - ⚠ The monoT5 checkpoints declare no licence on their cards.
   - Skip LLM listwise reranking as the default: GPT-4 did not beat monoT5-3B on SciFact [6].

4. **Inference-free learned sparse retrieval:** `opensearch-neural-sparse-encoding-doc-v3-distill`.
   - Licence: Apache-2.0.
   - Scores: SciFact 0.708, BEIR average 0.517 [13].
   - Cost: queries need only a tokenizer and a lookup table, so the model runs once per document at ingest.
   - ⚠ Storage is unverified. pgvector `sparsevec` HNSW indexes only up to 1,000 non-zeros [4], so the module may need its own inverted-index table.

5. **LLM query expansion.**
   - HyDE on SciFact: Contriever 64.9 → 69.1 (BM25 67.9) [14].
   - query2doc: +3–15% for BM25 on MS MARCO and TREC DL [15].
   - It costs one LLM call per query. SciFact queries are already declarative claims, so I'd expect a small gain (my inference). Measure before adopting.

### Skip (licence, or weak evidence)

| Item | Reason |
|---|---|
| ParadeDB `pg_search` | AGPL-3.0 [16] |
| VectorChord-bm25 | AGPL-3.0 or ELv2 [17] |
| SPLADE-v3 (SciFact 71.0, BEIR 51.7) | CC BY-NC-SA 4.0 [18]; the OpenSearch model in item 4 gives equal quality under Apache-2.0 |
| jina-reranker-v2 | CC BY-NC 4.0 [5] |
| EmbeddingGemma-300m | "Gemma" terms, not an OSI licence [5c] |
| docT5query | +0.010 over BM25 [1]; ⚠ model licence unstated |
| DPR, ANCE | 0.318 and 0.507 on SciFact [1] |

### Scientific claim verification: what the leaders used for retrieval

- **VeriSci (SciFact baseline):** TF-IDF retrieval, R@3 69.38 on dev [9].
- **VerT5erini:** BM25 → monoT5 → T5 sentence selection and labelling [9].
- **MultiVerS (MIT licence):** retrieval reuses VerT5erini's top 10 [19]. On the SciFact test set (abstract F1 / sentence F1) [19]:

  | System | Abstract F1 | Sentence F1 |
  |---|---|---|
  | MultiVerS | 72.5 | 67.2 |
  | ParagraphJoint | 69.1 | 60.9 |
  | VerT5erini | 68.2 | 63.4 |

- **SciFact-Open:** the corpus grows to 500K abstracts and systems lose at least 15 F1 [20]. Retrieval quality at scale is therefore the bottleneck for our paper-ingestion product.
- **LLM-based verification:** reported results mostly use gold abstracts on the dev set with mixed metrics, so they are not comparable. ⚠ An example is Zero-NatVer with Llama3-8B at macro-F1 54.58, which I saw only in a search snippet.
- **Data licences:** SciFact claims are CC BY 4.0 and abstracts ODC-By [21]. BEIR lists SciFact as CC BY-NC 2.0 [1]. ⚠ This conflict needs checking before shipping eval data.

### Agent memory: what actually wins

LongMemEval (ICLR 2025; MIT code) [22]:

| System | Model | Accuracy | Source |
|---|---|---|---|
| Full context | gpt-4o | 60.2 | Zep paper [23] |
| Zep (Graphiti temporal KG; Apache-2.0) | gpt-4o | 71.2 | Zep paper [23] |
| Full context | gpt-4o-mini | 55.4 | Zep paper [23] |
| Zep | gpt-4o-mini | 63.8 | Zep paper [23] |
| Mastra plain RAG, top-k 20 | gpt-4o | 80.05 | Mastra [24] |
| Mastra Observational Memory (no retrieval: a compressed observation log kept in context) | gpt-4o | 84.23 | Mastra [24] |
| Mastra Observational Memory | gpt-5-mini | 94.87 | Mastra [24] |

- Redis re-ran systems under one judge (Sept 2026) [25]:
  - Vendor numbers often do not reproduce, with gaps from +3 to −28 points.
  - The best reproduced GPT-4o system scored 86.5%. It combines:
    - hybrid retrieval (0.7·cosine + 0.3·BM25),
    - neighbouring-chunk expansion,
    - LLM-extracted facts given to the model alongside the raw excerpts.

LoCoMo, LLM-judge score, from Mem0's own paper [26]:

| System | Score |
|---|---|
| Full context | 72.90 |
| Mem0 with graph (Mem0g) | 68.44 |
| Mem0 | 66.88 |
| Zep | 65.99 |
| LangMem | 58.10 |
| OpenAI memory | 52.90 |
| A-MEM | 48.38 |

- Letta reports 74.0 for a plain filesystem-plus-search agent on gpt-4o-mini, and argues LoCoMo mostly measures retrieval [27].
- The LoCoMo data is CC BY-NC 4.0, so use it for internal evaluation only.

Conclusion: no graph database wins memory benchmarks. What wins is keeping both extracted facts and raw quotes (the kernel already stores claims with quotes), hybrid retrieval, time-aware filtering and session decomposition (LongMemEval's own optimisations [22]), and a good reader model.

Framework licences: Letta, Mem0 and Graphiti are Apache-2.0; A-MEM is MIT. Graphiti needs Neo4j, FalkorDB or Neptune, and its Kùzu backend is deprecated [28]. I'd skip it as a dependency and borrow its bi-temporal idea instead.

### Graph retrieval

HippoRAG 2 (MIT; personalised PageRank over an open KG built from passages) against the NV-Embed-v2 7B dense baseline [29]:

| Measure | NV-Embed-v2 | HippoRAG 2 |
|---|---|---|
| Average QA F1 | 57.0 | 59.8 |
| 2Wiki F1 | 61.5 | 71.0 |
| MuSiQue F1 | 45.7 | 48.6 |
| MuSiQue recall@5 | 69.7 | 74.7 |
| 2Wiki recall@5 | 76.5 | 90.4 |

Other structure-augmented methods all scored below plain dense retrieval on average F1 [29]:

| Method | Average F1 |
|---|---|
| GraphRAG (MIT) | 49.6 |
| RAPTOR (MIT) | 48.8 |
| LightRAG (MIT) | 6.6 |

Two systematic studies conclude that GraphRAG often underperforms vanilla RAG and helps mainly on multi-hop or reasoning questions [30][31].

Postgres options:
- PPR or k-hop expansion over `api` views, via recursive CTEs or in-module Python. This is the right first read-model.
- Apache AGE (Apache-2.0; Postgres 11–18; openCypher) [32] is optional, if Cypher is wanted.
- External graph stores: skip.

## (b) Staged plan

1. **SciFact first (biggest measured gain, lowest cost).**
   1. `recall-index-bm25` on pg_textsearch. Target ≈0.665–0.68 against today's 0.296 [1][6].
   2. Add a dense column (bge-base or gte-base) in pgvector with RRF/CC fusion. The dense model alone is self-reported at 0.74–0.77 [5].
   3. Add a cross-encoder rerank of the fused top 100 (Qwen3-Reranker-0.6B or bge-reranker-v2-m3). Evidence suggests mid-70s or better [6].
   4. Record nDCG@10 and R@3 per step.
   5. Then add a larger corpus check, since SciFact-Open shows a drop of at least 15 F1 at scale [20].
2. **Agent memory.**
   1. Index claims at two granularities: claim text, plus source quote or session chunk.
   2. Use hybrid retrieval with neighbour expansion.
   3. Add time-aware filters on valid and recorded time.
   4. Evaluate on LongMemEval_s (MIT) with a fixed judge; LoCoMo internally only.
3. **Graph (optional read-model).**
   1. Build an entity graph from claims and run HippoRAG-2-style PPR seeded by the top hits.
   2. Evaluate recall@5 on MuSiQue or 2Wiki subsets.
   3. Keep it only if it reproduces the multi-hop gains [29]; never put it on the default path.

## (c) Sources

| # | Source | URL | Code/model licence |
|---|---|---|---|
| 1 | Thakur et al., BEIR, NeurIPS 2021 D&B, arXiv 2104.08663 | https://arxiv.org/abs/2104.08663 | Apache-2.0 |
| 2 | pg_textsearch repo | https://github.com/timescale/pg_textsearch | PostgreSQL |
| 3 | Tiger Data, pg_textsearch 1.0 blog (2026-03-31) | https://www.tigerdata.com/blog/pg-textsearch-bm25-full-text-search-postgres | — |
| 4 | pgvector | https://github.com/pgvector/pgvector | PostgreSQL |
| 5 | HF model cards (BAAI/bge-*, intfloat/e5-*, Alibaba-NLP/gte-base-en-v1.5, mixedbread-ai/mxbai-embed-large-v1, nomic-embed-text-v1.5, Snowflake arctic-embed, jina-reranker-v2) | https://huggingface.co/BAAI/bge-base-en-v1.5 etc. | as stated |
| 5b | Qwen3-Embedding-0.6B, arXiv 2506.05176 | https://huggingface.co/Qwen/Qwen3-Embedding-0.6B | Apache-2.0 |
| 5c | EmbeddingGemma-300m | https://huggingface.co/google/embeddinggemma-300m | Gemma terms |
| 6 | Sun et al., "Is ChatGPT Good at Search?" (RankGPT), EMNLP 2023, arXiv 2304.09542 | https://arxiv.org/abs/2304.09542 | Apache-2.0 |
| 7 | Cormack, Clarke, Büttcher, RRF, SIGIR 2009 | https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf | — |
| 8 | Bruch, Gai, Ingber, "An Analysis of Fusion Functions for Hybrid Retrieval", TOIS, arXiv 2210.11934 | https://arxiv.org/abs/2210.11934 | — |
| 9 | Pradeep et al., VerT5erini, LOUHI 2021, arXiv 2010.11930 | https://arxiv.org/abs/2010.11930 | — |
| 10 | Qwen3-Reranker-0.6B | https://huggingface.co/Qwen/Qwen3-Reranker-0.6B | Apache-2.0 |
| 11 | mxbai-rerank-base-v2 | https://huggingface.co/mixedbread-ai/mxbai-rerank-base-v2 | Apache-2.0 |
| 12 | cross-encoder/ms-marco-MiniLM-L6-v2 | https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2 | Apache-2.0 |
| 13 | OpenSearch neural sparse doc-v3-distill | https://huggingface.co/opensearch-project/opensearch-neural-sparse-encoding-doc-v3-distill | Apache-2.0 |
| 14 | Gao et al., HyDE, ACL 2023, arXiv 2212.10496 | https://arxiv.org/abs/2212.10496 | — |
| 15 | Wang, Yang, Wei, query2doc, EMNLP 2023, arXiv 2303.07678 | https://arxiv.org/abs/2303.07678 | — |
| 16 | ParadeDB | https://github.com/paradedb/paradedb | AGPL-3.0 |
| 17 | VectorChord-bm25 | https://github.com/tensorchord/VectorChord-bm25 | AGPL-3.0 / ELv2 |
| 18 | Lassance et al., SPLADE-v3, arXiv 2403.06789 | https://huggingface.co/naver/splade-v3 | CC BY-NC-SA 4.0 |
| 19 | Wadden et al., MultiVerS, NAACL Findings 2022, arXiv 2112.01640 | https://github.com/dwadden/multivers | MIT |
| 20 | Wadden et al., SciFact-Open, EMNLP Findings 2022, arXiv 2210.13777 | https://arxiv.org/abs/2210.13777 | — |
| 21 | Wadden et al., SciFact, EMNLP 2020, arXiv 2004.14974 | https://github.com/allenai/scifact | data CC BY 4.0 / ODC-By |
| 22 | Wu et al., LongMemEval, ICLR 2025, arXiv 2410.10813 | https://github.com/xiaowu0162/LongMemEval | MIT |
| 23 | Rasmussen et al., Zep, arXiv 2501.13956 | https://arxiv.org/abs/2501.13956 | Graphiti Apache-2.0 |
| 24 | Mastra, Observational Memory (2026-02-09) | https://mastra.ai/research/observational-memory | ⚠ not checked |
| 25 | Redis, Agent memory benchmark report (2026-09-21) | https://redis.io/resources/agent-memory-benchmark-report/ | — |
| 26 | Chhikara et al., Mem0, arXiv 2504.19413 | https://arxiv.org/abs/2504.19413 | Apache-2.0 |
| 27 | Letta, "Benchmarking AI Agent Memory" (2025-08-12) | https://www.letta.com/blog/benchmarking-ai-agent-memory | Apache-2.0 |
| 28 | Graphiti README | https://github.com/getzep/graphiti | Apache-2.0 |
| 29 | Gutiérrez et al., HippoRAG 2 "From RAG to Memory", arXiv 2502.14802 | https://github.com/OSU-NLP-Group/HippoRAG | MIT |
| 30 | Han et al., "RAG vs. GraphRAG", arXiv 2502.11371 | https://arxiv.org/abs/2502.11371 | — |
| 31 | Xiang et al., "When to use Graphs in RAG" (GraphRAG-Bench), arXiv 2506.05690 | https://arxiv.org/abs/2506.05690 | — |
| 32 | Apache AGE | https://github.com/apache/age | Apache-2.0 |
| — | Others: Packer et al., MemGPT, arXiv 2310.08560; Xu et al., A-MEM, NeurIPS 2025, arXiv 2502.12110; Sarthi et al., RAPTOR, arXiv 2401.18059; Edge et al., GraphRAG, arXiv 2404.16130; Guo et al., LightRAG, arXiv 2410.05779; Maharana et al., LoCoMo, ACL 2024, arXiv 2402.17753 (data CC BY-NC 4.0) | arxiv.org/abs/<id> | MIT/Apache per repo |

Unverified items (⚠):
- whether pg_textsearch works correctly with RLS;
- how to store doc-v3-distill output in Postgres;
- the monoT5 and docT5query checkpoint licences;
- SciFact's licence (BEIR says CC BY-NC 2.0, the SciFact repo says CC BY 4.0 / ODC-By);
- LLM claim-verification numbers;
- Mastra's licence;
- vendor LongMemEval scores that Redis did not reproduce;
- HippoRAG 2's venue (I cite only its arXiv id).
