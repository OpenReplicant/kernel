# Briefing by hook, session start and AGENTS.md (M6-T7, not merged)

`briefing.patch` is the unmerged M6-T7 work, as a diff against main at c23a982. It covers:

- `brief()` with a relevance floor;
- the prompt hook and session start giving the briefing and what changed;
- `kk brief --write AGENTS.md`;
- their smoke checks.

It was parked when products moved to Cognee (D-94). Cognee's Claude Code plugin already recalls memory on each prompt and at session start. Apply it with `git apply` if the kernel is revived.
