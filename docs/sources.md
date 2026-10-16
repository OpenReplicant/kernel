# Sources and lineage

What the design borrows, and from where. Entries without a link were found during research but the
link wasn't recorded; search by name.

## Earlier design records (superseded, kept for history)

- Universal Agent Memory — research record: https://claude.ai/code/artifact/e1dc8c6c-1064-4254-b050-d464bf208c1e
- Governed Wiki Knowledge Core — the Wikibase-based design this spec replaces: https://claude.ai/code/artifact/7db9c138-c5cc-46e0-9a6e-790f3eff4398
- Starting point: "Agent memory explodes while RAG's favorite assumptions get a reality check" (dev.to) — https://dev.to/felipe0liveira/agent-memory-explodes-while-rags-favorite-assumptions-get-a-reality-check-2i60

## Claims, provenance, belief

| Idea | Source |
| --- | --- |
| Statements with qualifiers, references and ranks; supersede, don't delete | Wikidata / Wikibase data model |
| Qualifier and reference patterns for mapping ontologies onto statements | Shimizu et al., "Ontology Design Facilitating Wikibase Integration", J. Web Semantics 2024 — https://arxiv.org/abs/2205.14032 |
| Provenance vocabulary | W3C PROV-O — https://www.w3.org/TR/prov-o/ |
| Plans and steps linked to runs | P-Plan — https://www.opmw.org/model/p-plan/ |
| Quotes and spans in sources | W3C Web Annotation — https://www.w3.org/TR/annotation-vocab/ |
| Mapping tables with confidence | SSSOM — https://w3id.org/sssom/ |
| Evidence-backed claims, review status, candidate claims, authority separate from permission, `explain` | Provena — https://mcpservers.org/servers/admiralpunk/provena |
| Ontology generation, SHACL, PROV lineage, conflict resolution by credibility | Semantica — https://github.com/Hawksight-AI/semantica |
| Trust decay, contradiction surfacing, decision ledger | epistemic-graph-memory — https://pypi.org/project/epistemic-graph-memory/ |
| Confidence decay, supersession, consolidation tiers, crystallization, ingest filtering, hooks | LLM Wiki v2 (rohitg00 gist), following Karpathy's LLM wiki idea |
| Temporal knowledge graph, invalidation instead of deletion | Zep / Graphiti — https://github.com/getzep/graphiti |
| Expandable ontology in agent memory | cognee — https://github.com/topoteretes/cognee |
| Verifying a claim against its quoted source | ProVe (Wikidata reference verification) |
| Grounded SPARQL query agents | SPINACH |

## Ontologies and system modeling

| Idea | Source |
| --- | --- |
| Slots, fulfillment, node types/templates, substitution | OASIS TOSCA 2.0 (OASIS Standard, Sept 2025) — https://www.oasis-open.org/?p=13225 , spec https://docs.oasis-open.org/tosca/TOSCA/v2.0/cs01/TOSCA-v2.0-cs01.pdf |
| Interfaces, transports (forms), Thing Models | W3C WoT Thing Description 1.1 / 2.0 draft — https://www.w3.org/TR/2025/WD-wot-thing-description-2.0-20251104 |
| Variability models (deferred) | Universal Variability Language — https://research.jku.at/en/publications/third-tutorial-on-the-universal-variablity-language-practical-ext/ |
| BPMN as OWL (milestone 2 candidate) | BBO, BPMN 2.0 Based Ontology — https://hal.archives-ouvertes.fr/hal-02365012 |
| Ontology editing platform (reference, commercial) | metaphactory 6.0 — https://metaphactory.com/about/metaphactory/news-events/news/metaphactory-6-0-helps-organizations-connect-trusted-enterprise-knowledge-to-ai-applications |

## Interviews

| Idea | Source |
| --- | --- |
| Ontology-guided interviews: aspects → dimensions → slots, re-ranking, pruning | OntoAgent, "From Chat to Interview: Agentic Requirements Elicitation with an Experience Ontology" — https://arxiv.org/abs/2605.05828 |
| User stories and competency questions for ontology work | OntoChat |

## Processes, execution, self-adaptation

| Idea | Source |
| --- | --- |
| Engine owns the graph; agents act at tasks, gateways and hooks with bounded actions and permissions | CUGA FLO — https://arxiv.org/pdf/2606.27188 |
| Agentic BPM systems: data-driven, autonomy spectrum, five layers | Dumas, Milani, Chapela-Campa — https://arxiv.org/abs/2601.18833 |
| Compliance by construction; process model decides the next step, LLM fills typed outputs | Neuro-symbolic agents for regulated process automation — https://arxiv.org/pdf/2606.13405 |
| BPMN from text with people in the loop | Pragmos — https://www.emergentmind.com/papers/2604.27311 |
| LLM process modeling via pm4py | ProMoAI — https://arxiv.org/abs/2403.04327v2 |
| MAPE-K with generative AI; humans on the loop | Li, Zhang, Li, Weyns, Jin, Tei — https://papers.cool/arxiv/2512.04680 |
| Skill library learned from successful attempts | Voyager — https://arxiv.org/abs/2305.16291 |
| Embeddable Python BPMN engine | SpiffWorkflow — https://github.com/sartography/SpiffWorkflow |
| Java BPMN engines (alternatives) | Flowable; Operaton — https://www.itemis.com/en/blog/custom-software/full-stack/operaton-openbpm-camunda-7-end-of-life/ ; CIB seven — https://cibseven.org/en |
| Camunda 8 production licence requirement | https://camunda.com/blog/2024/10/camunda-licensing-what-you-need-to-know/ |
| Process mining, OCEL 2.0 | pm4py — https://pypi.org/project/pm4py |

## Runtime pieces

| Piece | Source |
| --- | --- |
| Chain runner | RuleGo — https://github.com/rulego/rulego ; RuleGo-Server — https://gitee.com/rulego/rulego-server ; components — https://github.com/rulego/rulego-components |
| Durable workflows on Postgres (considered, dropped from default) | DBOS Transact — https://github.com/DBOS-project/dbos-transact-py |
| Workflow platform on Postgres (considered; AGPL) | Windmill — https://cdn.jsdelivr.net/gh/windmill-labs/windmill@main/README.md |
| NOTIFY → WebSocket | pg_eventserv — https://github.com/crunchydata/pg_eventserv |
| Queues in Postgres (for modules) | pgmq — https://github.com/pgmq/pgmq |
| Agent Skills format | https://agentskills.io/home |
| A2A Agent Card | https://tyk.io/learning-center/a2a-protocol-architecture-and-technical-specification/ |
| MCP servers | see docs/mcp-servers.md |
