# The loop (S2): a room-climate entity

One entity, one module of each type, on a live stream: it senses a room, forms a belief, recalls
it, decides, acts, and senses the effect of its own action. Gate: `tests/test_loop.py`.

```
world/climate_sim.py ──reading──▶ climate_sense_reading ──▶ temperature, heater (observed)
        ▲                                                         │ outbox
        │                                                         ▼
  POST /heater                                   climate_comfort_update ──▶ comfort (inferred)
        │                                                                     │
climate_heater_switch ◀── decision (inferred) ◀── decide ◀── climate_recall_room
  (effector)                                    climate_control_tick: every second, per room
        │
        ▼
 heater_command (observed: the world's answer)
```

| Module | Type | Activation | What it does |
|---|---|---|---|
| `climate` | ontology | — | `room`; predicates `climate.temperature`, `.heater`, `.setpoint`, `.comfort`, `.decision`, `.heater_command` |
| `climate_sense` | perceiver | on each reading (the simulator posts to the chain); on command (setpoint) | A reading is kept unchanged as the source of observed claims (temperature always, heater state when it changes); a setpoint is stated by a person |
| `climate_comfort` | reasoner | on change: a new temperature or setpoint claim | `cold` / `ok` / `warm` against setpoint ± band; written only when it changes, `derived_from` the temperature and setpoint |
| `climate_recall` | recaller | on command | The held claims about a room as data and as text, with their ids |
| `climate_control` | reasoner | on schedule (RuleGo schedule endpoint, every second) | For each room (`for` node): recall → decide → record the decision → hand it to the effector |
| `climate_chat` | perceiver | on command (a person's words) | A model (S3, `docs/MODELS.md`) interprets the words into a setpoint, `inferred`, citing the model call |
| `climate_heater` | effector | on command (from a decision) | Switches the heater; acts only on a held decision for that room and state, at most once per `min_interval`; records the request and the world's answer as an observed `heater_command` derived from the decision |

**Policy:** cold → heater on; warm → off; ok → leave it. The heater's current state is the
later of the observed state and the entity's own last decision, so a decision not yet visible
in a reading isn't repeated.

**Provenance of an action.** A decision is `inferred`, cites the recalled context (source kind
`recall`, the text and state the policy saw) and is `derived_from` the comfort belief and the
heater observation it used. The comfort belief cites the change that caused it and is derived
from the temperature observation (whose source is the raw reading) and the stated setpoint
(whose source is the person's command). `claims.trace(decision)` walks all of it.

**Concurrency.** Schedule ticks can overlap (a tick takes longer than a second while the Python
workers are busy). Two ticks recalling the same state would both record a decision, so the
decision is written with `if_current` (compare-and-set on the held decision); the loser's
decision is skipped and it doesn't act.

**Known limits.**
- The effector acts, then records: the record holds the world's answer. If the write failed
  after the world switched, the action would be missing from the graph (its decision, recorded
  before acting, still shows the intent).
- One room's control is serialized by the compare-and-set, not by a lock across the whole
  recall → act sequence.

## Running it by hand

```sh
KERNEL_GRAPH=entity tools/dev.sh up
export DATABASE_URL=postgresql://agens@127.0.0.1:55432/postgres KERNEL_GRAPH=entity
python -c "from kernel import db, modules; c = db.connect(); db.install_core(c); \
  [modules.install(f'modules/{m}', c) for m in ('climate_sense', 'climate_comfort', 'climate_control')]"
python -m kernel.bridge &
python world/climate_sim.py --rooms lounge=18,study=20 &
curl -X POST localhost:9090/api/v1/rules/climate_sense_setpoint/execute/COMMAND \
  -d '{"room": "lounge", "celsius": 21, "by": "me"}'
curl localhost:9191/state
```
