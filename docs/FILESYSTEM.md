# Filesystem layout

Three places hold everything, each with one job:

| Store | Holds | Rule |
|---|---|---|
| **Git (this repo)** | Code, chains, registry, compiled paper folders, prompts, docs | Anything a person reviews or that defines behavior |
| **Data root (`$PC_DATA`)** | Sources (documents, transcripts, logs, run traces), pinned paper repos, run artifacts, benchmark data, caches | Bulk and generated files; never edited by hand |
| **Postgres** | The kernel (`kb.*`: sources, spans, nodes, assertions) and runtime tables (`run.*`) | Rows point to files by path **and** content hash; files never point to rows |

Postgres never stores large blobs. A file can always be found from its row, and its hash proves
it hasn't changed.

## Data root

```
$PC_DATA/                         default ./data, git-ignored
├── sources/                      immutable inputs, content-addressed
│   └── sha256/ab/cd/abcd….pdf    one file per source; name = hash
├── repos/                        paper code at pinned commits, read-only
│   └── <owner>__<repo>@<commit>/
├── bench/                        benchmark datasets, by name and hash
│   └── humaneval/<sha256>/HumanEval.jsonl
├── runs/                         everything a run produces
│   └── <run_id>/
│       ├── run.json              toggles, models, budget (mirror of run.run)
│       ├── trace.jsonl           run.trace exported at the end; stored as a source
│       └── tasks/<task_id>/ep-<n>/
│           ├── work/             sandbox mount: the only dir a container sees
│           ├── solution.py
│           └── logs/             stdout, stderr per step
└── cache/                        disposable; safe to delete
```

## Rules

- **Sources are immutable.** A changed web page or PDF is a new file with a new hash. Write
  once, then make read-only. `kb.source` records hash, path, origin URI, media type, license
  and retrieval time; `kb.span` addresses passages inside it.
- **Paper repos are read-only clones** at the commit pinned in the spec. Adapters live in the
  repo's `papers/<id>/` folder, never inside the clone.
- **Sandboxes see one directory.** A container mounts only its episode's `work/` folder. No
  other path from the data root or the repo is visible to it.
- **Run folders are append-only.** A rerun of a finished step reuses its files; it never
  overwrites them.
- **Paths in Postgres are relative to `$PC_DATA`**, so the data root can move (to another disk,
  or later to object storage with the same key scheme) without rewriting rows.

## Exported views in git

Files such as `papers/reflexion/spec.yaml`, `SPEC.md` and `REPORT.md` are **views** exported
from kernel contexts. They are committed so people can review them, but the kernel is the
source of truth: regenerate them rather than editing them by hand.
