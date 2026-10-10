# Vision

## The long-term loop

1. **A general world-modeling kernel** holds knowledge as assertions with provenance, so any
   claim can be traced to the evidence behind it.
2. **Empirical papers are ingested as evidence.** Each paper is a source; its claims and
   results become assertions conditioned on how they were obtained.
3. **The first application compiles each paper into a design spec**: its mechanism mapped
   onto shared architectural primitives (slots), with implementation details, assets and
   wiring, in a format that composes with other papers' specs.
4. **The paper's own evaluation is reproduced** (and others can be run), and the results flow
   back into the kernel as new evidence about what works, under which conditions.
5. **Agents use that evidence to choose and compose building blocks**, building
   highly capable or custom-fit systems and improving themselves from measured results
   rather than isolated guesses or blind trial and error.

The application is recursive: the compiler is itself an agent built from the same
primitives, so its own design can eventually be specified, evaluated and improved the same way.

## Where version 1 sits

Version 1 builds the kernel (step 1) as the foundation, with its interfaces, and the first
application on top of it: one paper ingested as evidence (step 2), compiled into a decomposed
system (step 3), run and evaluated with results stored back as evidence (step 4). Only
evidence-driven composition by agents (step 5) is deferred.

The kernel comes first so its interfaces are shaped by a general model of the world rather
than by one application. The compiler is the first test of whether those interfaces are
general enough: it may use only what the kernel exposes.

## What version 1 sets up for step 5

- **Every result is a conditioned assertion**: technique × benchmark × model × budget →
  outcome, traceable through `why` to runs and to the paper's own claims.
- **Slots are decomposed, not opaque**, and registry components are kernel things with
  capabilities, so an agent can query what fills a role and how well it did.
- **Scripts are tool-shaped** (JSON Schema in and out), so agents can later use the kernel and
  platform through MCP.

## Cautions for the self-improvement step

- **Evidence is conditional.** A technique that helped GPT-4 on HumanEval in 2023 may not help
  a current model. Results only transfer as far as their conditions match.
- **Mechanism present is not benefit shown.** Mechanism tests prove a design was implemented;
  benchmarks show whether it helped. Keep the two separate in the evidence.
- **Optimizing against benchmarks invites overfitting and contamination.** Keep held-out
  evaluations that the improving agent never sees.
- **Promotion stays gated.** Components and design changes enter the registry only after
  passing tests, and with a human review step until the loop has earned trust.
