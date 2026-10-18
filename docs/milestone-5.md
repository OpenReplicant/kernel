# Milestone 5 — maps from conversations, interactions and code

**Why this, now** (D-80). Milestone 4's demo starts from an event log and a BPMN file a customer already
has. Most organisations have neither for most of their work: it lives in people's heads. So the next
demo builds the map by talking. An agent interviews people and records the organisation (who holds which
post, who reports to whom) and its processes (steps, order, hand-offs, limits) as claims. Each claim
quotes the sentence it came from, the map grows live in the UI, and the agent asks next about what the
map does not yet cover. The same encoders take in any real-time interaction: a chat, an agent working
through files and APIs, a robot or avatar acting and sensing.
Coding agents get the same treatment at a lower level: a code map of files, symbols, calls, imports
and inheritance, used both deterministically (traversal) and in the model's context.

Owner, Oct 2026: "expand the salable demo toward building business process and organizational maps from
interactive chat"; "consider other real time interactions like an agent navigating a file system or apis…
or piloting a robot/avatar"; "encoders that support lower levels of mapping"; "a range of internal
processors … might even run as triggers so that as soon as a pattern forms they are invoked"; target
smaller models for benchmarks. Research: `docs/interaction-research.md`.

Rules carry over: kernel v0 stays frozen; modules run sandboxed under the operator's policy (D-55);
standard vocabularies first (D-40); permissive licenses (D-11); people decide, models propose.

## Tasks

| ID | Task | Check | Status |
| --- | --- | --- | --- |
| M5-T1 | **What each module takes in and gives out** (owner: "be clear about what formats or modalities the encoders are fit for, along with what they output, and the reverse on decoders"): every manifest's `accepts` and `produces` is a media type, a pack term, an op kind or a kernel record; `docs/modules.md` is generated from the manifests | the catalog check fails on an unknown term or a stale page (CI) | done (D-78) |
| M5-T2 | **Processors woken by patterns** (owner: "as soon as a pattern forms they are invoked"): a `pattern` trigger (`S P O` over the claims an applied change set adds) in the module manifest; the worker library wakes a processor only when one fits | conformance, the process learner and anomaly declare their patterns; the loop still passes end to end | done (D-79) |
| M5-T3 | **Interaction encoder**: transcripts of chats and agent sessions → claims. A message is an ActivityStreams `as:Note` (actor, `as:inReplyTo`, `as:context` the conversation) stored as an artifact; a tool call, file or API action is a `prov:Activity` that used and generated entities (OpenTelemetry GenAI fields as the input map). Inputs: chat JSON lines, Claude Code session transcripts, OpenTelemetry GenAI spans; robot or avatar commands and readings map to SOSA actuations and observations | fixture transcripts give the expected claims; the same transcript again changes nothing; secrets are filtered first | done (D-81): chat and Claude Code transcripts; every quote occurs in the stored transcript; a 52 MB session in 64 s with its secrets left out |
| M5-T4 | **The interviewer** (a skill and a processor): the agent interviews a person and records the organisation in W3C ORG (`org:Organization`, `org:OrganizationalUnit`, `org:Post`, `org:Membership`, `org:reportsTo`) and processes in BBO, each claim extracted with the quote from the conversation (the quote verifier checks it). A gap processor, woken by patterns, lists what the map lacks (a step with no role, a role nobody holds, a hand-off with no next step), and the skill asks about those next | a scripted interview builds the expected org chart and process; every claim cites its utterance; the gaps shrink as it answers | done (D-82): five answers, 42 claims, every quote supported by the verifier; gaps 1, 6, 2, 1, 0 |
| M5-T5 | **The interview demo**: chat on one side, the organisation and the process maps growing on the other (an Organisation view; the Processes view), with the review lane for what the agent inferred; `kk demo --interview` | headless UI: a scripted conversation fills both maps, and a person confirms or corrects in place | |
| M5-T6 | **Interview eval with small models**: PET (MIT, 45 process texts) as simulated interviews, with an LLM answering as the expert from the text; a synthetic company for org charts (after Zuin et al.); models under 10B through OpenRouter free endpoints or local servers | scores recorded as DQV claims (evaluator); compared against extracting from the text directly | |
| M5-T7 | **Code map encoder** (lower than packages): with tree-sitter (MIT) tags, the files, classes, functions, imports, inheritance and name-resolved calls of a repository; entities named by SCIP symbol strings; a small code pack using Kythe's edge names; SPDX 3 for files, snippets and tests; each claim referenced by its file and line range. SCIP indexers (scip-python, scip-typescript) as an optional second method. Deterministic tools over MCP: definition, references, callers, callees, containment | fixture repositories give the expected symbols and edges; a changed file supersedes its claims; the tools answer from claims with belief | |
| M5-T8 | **Localization eval**: SWE-bench Lite localization (LocAgent's protocol) with a 7B model: BM25 recall, plus graph expansion, dense, dense plus graph | file and function Acc@k recorded; kept only if the graph gains | |
| M5-T9 | **Distributions** (D-84): a distribution manifest (modules, packs, skills, views, demo data) checked like module manifests; `kk up --distribution <name>` installs one as its own stack; the existing demos become distributions (`process-maps`, `devops`, `papers`), and `org-maps` is the first one built for sale | each distribution installs from a fresh checkout and passes its own gate; the catalog lists them | |

## Gates

| Gate | Check | Status |
| --- | --- | --- |
| M5-G1 | **A map from a conversation**: from a fresh checkout, `kk demo --interview`; a scripted interview yields an org chart and a process map, every claim citing the utterance it came from; a person corrects one in the UI and the map follows | |
| M5-G2 | **A code map helps a coding agent**: on SWE-bench Lite localization, recall with the code map beats recall without it, with a small model | |
| M5-G3 | Every new module installs with `kk module install` under the default policy; the kernel tests and all earlier gates still pass | |

## Later

Robot and avatar control loops beyond encoding (SOSA actuations through an actuator); SOMA as an optional
pack; the BPMN runner; the Learn phase for operations; hosting beyond one machine.
