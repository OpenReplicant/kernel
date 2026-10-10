# Backlog

Ideas kept for after version 1 proves the concept, roughly in the order they'd be needed.
Add new ideas here instead of building them.

- [ ] Durable jobs: spike gflow-engine against a killed-worker test, else a `run.job` table polled by a RuleGo chain
- [ ] OpenTelemetry through RuleGo's OpenTelemetry component, with GenAI spans from scripts and a Collector
- [ ] Snapshot, restore and branching for search papers (Tree of Thoughts, LATS)
- [ ] Record/replay gateway for external services and repeated model calls
- [ ] Long-running containerized script service if per-step start-up shows in timings
- [ ] Ingestion beyond papers: segmenters, entity resolution, vocabulary proposals
- [ ] Lab: toggle grids, cost reports, shared benchmarks
- [ ] Review interface for spec and reproduction checkpoints
- [ ] License checks on prompts and paper code before releasing compiled specs
- [ ] Go ports of hot scripts; Kubernetes driver
- [ ] Check the taxonomy against the 13 coding-agent scaffolds in arXiv 2604.03515; add loop driver, model routing and compaction strategy as slot attributes where they don't already fit
- [ ] Compile AgentSquare's 16 agents as a comparison set for the paper
- [ ] Harder benchmark than HumanEval if the baseline saturates it
- [ ] Platform MCP server: RuleGo's MCP endpoint exposing chains for registry search, spec validation, running chains, reading traces
- [ ] Move tracing, checkpoints and budget checks into rule-engine aspects (Go) if M0 confirms core AOP
- [ ] Stronger sandbox isolation (gVisor or Kata runtime under Podman) before running untrusted paper repos at scale
- [ ] Object storage behind the same `$PC_DATA` key scheme
- [ ] Compile the compiler: describe the compile-paper pipeline itself as a spec, so it can be improved by the same process
- [ ] AGE graph projection and pgvector search as derived views over `kb.assertion`
- [ ] Evidence-driven composition: an agent that queries observed results to choose and combine components for a new task
