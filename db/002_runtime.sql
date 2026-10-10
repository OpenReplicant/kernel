-- Operational tables: high-volume, not assertions. Outcomes from these tables are
-- written back to the kernel as 'observed' assertions (docs/KERNEL.md).

create schema if not exists run;

create table run.run (
  id           uuid primary key,
  context      uuid not null references kb.node,     -- the run's kernel context (conditions live there)
  system       uuid not null references kb.node,     -- the system context being run (a compiled spec)
  toggles      jsonb not null default '{}',
  budget_usd   numeric(10,4) not null,
  spent_usd    numeric(10,4) not null default 0,
  status       text not null default 'running' check (status in ('running','done','failed')),
  artifact_dir text,                                  -- runs/<id>, relative to $PC_DATA
  started_at   timestamptz not null default now(),
  finished_at  timestamptz
);

create table run.step (
  run_id      uuid not null references run.run,
  step_key    text not null,
  output      jsonb not null,
  usage       jsonb,
  finished_at timestamptz not null default now(),
  primary key (run_id, step_key)
);

create table run.trace (
  id       bigserial primary key,
  run_id   uuid not null references run.run,
  at       timestamptz not null default now(),
  task_id  text,
  episode  int,
  step_key text,
  event    text not null,
  slot     text,
  port     text,
  payload  jsonb
);
create index on run.trace (run_id, task_id, episode, id);
create index on run.trace (run_id, event);

-- Working memory of a running agent (memory slots). Runtime state, not knowledge.
create table run.memory (
  run_id  uuid not null references run.run,
  task_id text not null,
  slot    text not null,
  seq     int  not null,
  item    jsonb not null,
  primary key (run_id, task_id, slot, seq)
);
