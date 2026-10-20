# Process interview, on Cognee

An agent interviews the people who do the work. From what they say it builds the **organisation map**
(teams, posts, who holds them, who reports to whom) and the **process map** (steps, their order, who does
each). Every line of either map quotes the sentence it came from. What the maps still lack is listed as
the next questions to ask. Each process exports as BPMN, with a lane per post, for any BPMN editor.

[Cognee](https://github.com/topoteretes/cognee) (Apache-2.0, pinned to 1.6.3) does the memory: storage,
graph, embeddings, search, datasets and permissions. This distribution adds what an interview needs (D-94):

| Part | What it does |
| --- | --- |
| `vocab.py` | the kinds (organisation, unit, post, person, process, step) and relations, each with its W3C ORG or BBO term, and the kinds each relation joins |
| `extract.py` | one answer to facts: a model proposes them (Cognee's LLM settings), code keeps what it can check: the quote occurs in the answer, each name was said or is on the map, kinds fit the relation |
| `graph.py` | answers as Cognee DataPoints: the message kept verbatim; each relation an edge with `quote`, `said_by`, `at` and `message`; stable ids, so a later answer adds to the same post or step |
| `gaps.py` | what the maps lack, as questions (who does this step, what comes after it, who holds this post...) |
| `bpmn.py` | a process as BPMN 2.0 with a diagram |

```sh
pip install -r distributions/process-interview/requirements.txt
cd distributions/process-interview
# Any model Cognee supports, e.g. through OpenRouter:
export LLM_PROVIDER=custom LLM_ENDPOINT=https://openrouter.ai/api/v1 LLM_MODEL=openrouter/<model> LLM_API_KEY=...
export EMBEDDING_PROVIDER=fastembed EMBEDDING_MODEL=BAAI/bge-small-en-v1.5 EMBEDDING_DIMENSIONS=384
python -m process_interview tests/fixtures/acme.jsonl --dataset acme --answers answers.jsonl --bpmn out/
```

The transcript has one message per line: `{"id", "conversation", "author", "kind": "person" | "agent",
"time", "text"}`. A second transcript into the same dataset continues the same maps.

## Checks

```sh
python tests/test_interview.py            # the scripted Acme interview, the checks, the BPMN (no Cognee needed)
python tests/test_interview.py --cognee   # the same interview through Cognee and read back from its graph
python eval/run.py ANSWERS.jsonl --label NAME   # score a model's answers (eval/answers/) against the expected maps
python eval/run.py --prompts              # the five prompts, for a sub-agent to answer
```

The scripted interview gives the same open questions after each answer as the kernel's interview did
(`modules/skill-interview/tests/fixtures/acme.yaml`).

## First results (Acme, 16 expected facts; Oct 2026)

| Model | Found | Right | Facts dropped by the checks |
| --- | --- | --- | --- |
| scripted (a model that answers well) | 16/16 | 16/16 | 0 |
| Haiku, as a sub-agent (`eval/runs/2026-10-20-haiku-1.json`) | 11/16 | 11/12 | 0 |
| `liquid/lfm-2.5-2.6b:free`, through Cognee (`eval/runs/2026-10-20-lfm-2.6b-1.json`) | 1/16 | 1/2 | 7 of 9 |

One interview of five answers is a smoke test, not a benchmark. Haiku's misses are mostly steps never
attached to their process, because the answer never names it. The interviewer knows which process it
is asking about, so the next step is to give that to the extractor. A 2.6B model cannot do this task;
the checks keep 7 of its 8 mistakes off the maps (one person was recorded as a post).

## Not yet

- The interviewer itself: a skill and MCP tools (`record`, `questions`, `bpmn`) next to Cognee's own MCP server.
- Corrections: a later answer that contradicts an earlier one should replace it, keeping both quotes.
- Review: a person approves the maps before they count.
- Event logs and documented models (from `modules/encoder-eventlog`, `encoder-bpmn` and `conformance`) in
  the same dataset, so the interview, the documented process and the logs can be compared.
