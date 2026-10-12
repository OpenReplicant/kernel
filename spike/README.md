# spike/ — S0, throwaway

Evidence for `docs/SPIKE_NOTES.md`. Not the kernel; S1 ports ideas from here deliberately.

| Path | What |
|---|---|
| `agensgraph/` | Dockerfile + `build.sh`: AgensGraph v2.18.4.0 image with pgvector v0.8.7 compiled in |
| `sql/01_graph.sql` | graph `s0`, `doc` vertices with a promoted `vector(3)` + HNSW index, topics, `about` edges |
| `sql/02_outbox.sql` | outbox table and per-row triggers on `doc` and `about` |
| `rulego/` | RuleGo-Server module (v0.38.0 + `x/python`) and config, from `second_iteration` |
| `chains/s0_on_change.json` | the chain the bridge calls: one `x/python` node |
| `steps/classify.py` | the step: nearest classified neighbour by vector, write an `about` edge |
| `bridge.py` | outbox → RuleGo: LISTEN + catch-up, mark done, retry |
| `run.sh` | bring everything up from scratch |
| `test_gate.py` | the S0 gate |

```sh
python3 -m venv .venv && .venv/bin/pip install "psycopg[binary]>=3.2" requests pytest
spike/run.sh   # needs dockerd running
DATABASE_URL=postgresql://agens@127.0.0.1:55432/postgres PATH=$PWD/.venv/bin:$PATH \
  pytest -v spike/test_gate.py
```
