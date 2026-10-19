# Work with us

The Knowledge Kernel is open source (Apache-2.0). You can run it yourself. We also build on it for
organisations that want results sooner, or something made for their own processes and systems.

## What it does that other tools don't

Every number and every statement it shows can be clicked through to the source behind it: the log
line, the sentence in a procedure, the message in an interview. It shows how sure it is. A person
approves what models propose before it counts. Who may see what is enforced by the database, not by
a model, and nothing is ever silently overwritten. It runs on your own machines.

## Engagements

### 1. Process pilot: documented against actual (2–4 weeks)

Bring an export from the system that runs the process (an ERP, ticketing or workflow tool; CSV or XES),
the documented model if one exists (BPMN), and the written procedure. You get:

- the process as it really runs, next to how it is documented;
- the deviations that matter, ranked, each with the cases behind it;
- a process model that learns from what your people approve;
- the documented model exported back as BPMN;
- answers to questions about the process, each citing its evidence.

Where nothing is written down, we interview the people who do the work. The organisation chart and the
process map are built from what they say, each line quoting them, and the gaps are listed as the next
questions. Proven on BPI Challenge 2019, a real purchasing process of 1.6 M events (`kk demo --bpi`).

### 2. Self-healing operations: the MAPE-K loop (pilot on one service)

The same kernel closes the loop on running systems:

- **Monitor:** metrics are recorded as observations.
- **Analyse:** anomalies are detected.
- **Plan:** an agent proposes an adaptation.
- **Execute:** a person approves it, and an actuator carries it out.
- **Knowledge:** every step is recorded and cited.

The process model and the operational model share one store, so a deviation in the business process
and a fault in the system behind it can be traced to each other. Today the actuator drives Docker
Compose on one host. A pilot adds the actuators your platform needs.

### 3. Memory for your agents

Memory for your agents, with provenance and clearance. They recall what was decided, by whom and from
what source, across sessions and across agents. Every answer is cited. People review what agents
propose. It connects to any harness that speaks MCP (Claude Code and others) in one command (`kk up`).

### 4. Custom modules and distributions

The system is modular. We build:

- encoders for your formats and systems;
- views for your teams;
- connectors to your tools;
- a distribution: a preset stack for one job, installed per team or per client.

## How an engagement runs

1. **A short call:** your process or system, and the data that exists.
2. **A fixed-scope pilot** on your data, on your machines or a server you control. The result is a
   running system and a findings report in which every number is cited.
3. **Then your choice:** build further, hand over to your team with support, or stop. The code stays
   yours to run, under Apache-2.0.

## What is ready and what a pilot adds

| Ready today | Built in a pilot |
| --- | --- |
| event logs (CSV, XES), BPMN, written procedures, papers, chats and agent sessions as sources | connectors to your systems |
| documented against actual; deviations with evidence; a model that learns from approvals | your process's specific rules and KPIs |
| interviews that build org charts and process maps, quoted | an interview programme across your teams |
| the MAPE-K loop with a Docker Compose actuator on one host | actuators for your platform; hosting beyond one machine |
| sign-in per person, database-enforced visibility, review lane, audit trail | your identity provider and policies |

## Get in touch

Open an issue at https://github.com/OpenReplicant/kernel with "Pilot:" in the title, saying what you
would like to see. Or try it first: `docs/demo.md` takes twenty minutes.
