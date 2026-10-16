# Kickoff

1. Create a new, empty git repository and copy the contents of this folder into it (CLAUDE.md at the root).
2. Open it in Claude Code and paste the prompt below as the first message.

---

Read CLAUDE.md, then docs/spec.md, docs/milestone-1.md and docs/decisions.md. We're starting milestone 1.

1. **T0.** Commit the handoff as-is. Set up CI that runs `kernel/schema/tests/run.sh` against a Postgres 16+
   service, `shapes/tests/test_shapes.py` and `scripts/check_manifests.py`. All three pass on the handoff
   as delivered; get CI green before anything else.
2. **T1 spike.** Look up the current RuleGo-Server release and docs (don't rely on memory), run it locally,
   and find out whether its MCP mode can pass the caller's identity into a chain. Timebox one day. Write
   the result into docs/decisions.md as the answer to O-7, and tell me what you found.
3. Stop and show me: the CI result, the spike result, and your plan for T2–T5 with the exact image and
   package versions you intend to pin.

Before you change anything under `kernel/schema/`, stop and ask me: kernel v0 is frozen. The items marked
"adopted — confirm" in docs/decisions.md are defaults I haven't confirmed yet; list them in your first
report so I can confirm or change them.
