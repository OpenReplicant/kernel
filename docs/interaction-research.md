# Research for milestone 5: maps from conversations, interactions and code

Surveyed October 2026 (D-80). Licences decide what can ship (D-11): permissive or LGPL in the default
distribution. Anything else is either downloaded at evaluation time or used as a reference only.

## 1. Knowledge graphs and process models from interviews and conversation

No paper exactly matches "an LLM interviews a person and writes RDF, with a public benchmark". The
closest work falls into two groups: interview agents, and conversational process modelling.

| Work | What it does | Evaluation | Use for us |
| --- | --- | --- | --- |
| Zuin et al., *Leveraging LLMs for Tacit Knowledge Discovery in Organizational Contexts*, IJCNN 2025, arxiv.org/abs/2507.03811 | An agent moves through a company graph, interviews employees and rebuilds what they know step by step | 864 simulated companies; 94.9% full-knowledge recall; no code or data released | The simulation design: an LLM plays each employee, so org-map recall can be scored without people |
| Radhakrishnan, *Documenting SME Processes with Conversational AI: From Tacit Knowledge to BPMN*, LODISA 2025, arxiv.org/abs/2512.05122 | A chatbot interviews a worker and builds BPMN step by step, shown live in bpmn-js | One proof of concept, no metrics | The demo shape: chat on one side, the process map growing on the other |
| Klievtsova, Mangler, Kampik et al., BPMN-Chatbot and BPMN-Chatbot++, 2024–25, ceur-ws.org/Vol-3758/paper-15.pdf, ceur-ws.org/Vol-4032/paper-39.pdf | Conversational process modelling from text or voice | A subset of PET (21 descriptions, each with 8–11 human models) | The evaluation protocol |
| Jin et al., *OntoAgent*, RE 2026, arxiv.org/abs/2605.05828 | An ontology chooses the next interview question by ranking the concerns not yet covered | +33% IRE, +21% TKQR, a user study | Choose the next question from what the kernel's claims do not yet cover |
| OntoChat, arxiv.org/abs/2403.05921 | Conversational ontology requirements: user stories, competency questions, testing | Re-engineering the Music Meta Ontology | Competency questions as the interview's checklist |
| ProMoAI (van der Aalst's group), arxiv.org/abs/2403.04327 | Text to POWL to BPMN, refined from user feedback | 20 processes, 16 LLMs | Reference only: AGPL-3.0 |
| PROV-AGENT, arxiv.org/abs/2508.02866 | Extends W3C PROV to MCP agent interactions | — | The pattern for recording an agent's tool calls |

## 2. Vocabularies

| Area | Adopt | Namespace | Licence | Mapping only |
| --- | --- | --- | --- | --- |
| Organisations and roles | **W3C ORG** (Organization, OrganizationalUnit, Post, Membership, role, reporting) and foaf:Person | `http://www.w3.org/ns/org#` | W3C Recommendation 2014 | schema.org Organization and Role (CC BY-SA: do not vendor) |
| Messages and actions | **ActivityStreams 2.0** (Note, Create, inReplyTo, context, actor) | `https://www.w3.org/ns/activitystreams#` | W3C Recommendation 2017 | SIOC, schema.org Message and Conversation (Conversation is still pending) |
| Agent tool calls, file and API activity | **PROV-O** (already a pack): a tool call is a prov:Activity that used and generated entities | — | — | OpenTelemetry GenAI semantic conventions (`invoke_agent` and `execute_tool` spans, `gen_ai.*`) as the field map when ingesting; pin a version |
| Robots and avatars | **SOSA/SSN** (already a pack): sosa:Actuation for commands, sosa:Observation for sensing | — | — | SOMA (LGPL-3.0, could ship as an optional pack later); IEEE 1872 CORA (paywalled) |
| Dialogue acts | none: no maintained RDF standard (ISO 24617-2 is XML and paywalled) | — | — | qualifiers if needed |

## 3. Code maps

**Extractors:**
- **tree-sitter** (py-tree-sitter 0.26, MIT) with **tree-sitter-language-pack** (MIT, 1.22.1, rejects copyleft grammars). Its `tags.scm` queries give definitions and references in-process from Python, deterministically, for many languages. This is what LocAgent, RepoGraph, Aider's repo map, code-graph-rag (MIT) and codegraph (MIT) build on.
- **SCIP** indexers, for compiler-grade precision as an optional second method with higher belief: scip-python (MIT), scip-typescript (Apache-2.0).

**Excluded:**
- CodeQL (proprietary for private code)
- GitNexus (PolyForm Noncommercial)
- Serena's app layer (GPL-3.0; its solidlsp layer is MIT)
- universal-ctags (GPL-2.0)
- stack-graphs (archived September 2025)

**Vocabulary:**
- **SCIP symbol strings** as entity identifiers: `<scheme> <manager> <package> <version> <descriptors>`. The package part matches our Package URLs.
- **Kythe** edge names for code relations (`defines/binding`, `ref`, `ref/call`, `ref/imports`, `childof`, `extends`, `overrides`).
- **SPDX 3.0.1** for files, snippets (line ranges) and tests (`software:File`, `software:Snippet`, `contains`, `hasTest`).
- A snippet's range is also the claim's reference and quote.

**What graphs buy, measured** (SWE-bench Lite localization, 300 issues, MIT; LocAgent, arxiv.org/abs/2503.09089, Apache-2.0):

| Method | File Acc@5 | Function Acc@10 |
| --- | --- | --- |
| BM25 | 61.7 | 36.9 |
| CodeRankEmbed (137M) | 84.7 | 58.8 |
| LocAgent graph tools, Qwen2.5-7B fine-tuned | 88.3 | 71.5 |
| SweRank-Embed-Small (137M, no graph; arxiv.org/abs/2505.07849) | 90.9 | 74.5 |
| SweRank-Embed-Small + 7B re-ranker | 94.5 | 86.1 |

A strong small retriever matches the graph agent. SpIDER (arxiv.org/abs/2512.16956) still finds that expanding dense retrieval along containment and call edges adds 13–27% relative Recall@20. So the graph is a recall expander and a navigation tool, not a substitute for good retrieval.

## 4. Benchmarks for small models

| Benchmark | Measures | Licence | How we use it |
| --- | --- | --- | --- |
| **PET** (huggingface.co/datasets/patriziobellan/PET) | 45 process texts annotated with activities, actors, gateways and flows | MIT | Ships. Turn each text into a simulated interview (an LLM answers as the expert from the text) and score the extracted BBO claims |
| BEF4LLM (arxiv.org/abs/2601.21787) | 105 text-to-BPMN pairs, 39 metrics; tests models of 1–14B parameters | mixed: LRE set GPL-3.0, Camunda set unclear | Download at eval time only |
| ABCD (github.com/asappresearch/abcd) | ~10k customer-service dialogues with gold workflows | MIT | Dialogue to procedure |
| Org charts from conversation | no public gold set | — | Synthesise one (Zuin et al.): generate a company, simulate employees, interview them, score org:Membership and reporting recall |
| SWE-bench Lite localization (LocAgent's evaluation) | file and function Acc@k | MIT / Apache-2.0 | Our recall alone, then plus graph expansion, with a 7B model |
| LocBench | localization beyond bug reports | Apache-2.0 | Second check |
