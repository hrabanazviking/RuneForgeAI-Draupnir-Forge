# RuneForgeAI - Draupnir Forge
## Product and Architecture Specification for a Black-Box Mythic Engineering Vibe-Coding System

**Status:** Future Project / Concept Specification  
**Project Type:** AI-native software engineering orchestrator  
**Parent Methodology:** Mythic Engineering  
**Working Name:** **Draupnir Forge**  
**Suggested Repository:** `RuneForgeAI-Draupnir-Forge`  
**Primary Goal:** Allow a human to describe what they want built at a high level, then automatically execute a disciplined Mythic Engineering process through repeated AI-agent loops until the project is complete, blocked, or requires human judgment.

---

# 1. Core Idea

**Draupnir Forge** is a deliberately simple, almost black-box vibe-coding application whose internal operation is substantially more rigorous than its surface suggests.

The user should be able to begin with something close to:

> Build me a local-first personal research assistant that can index Markdown notes, answer questions from them, and preserve citations.

The visible experience can remain simple:

1. Describe what should exist.
2. Answer only genuinely necessary questions.
3. Approve or modify the interpreted goal.
4. Let the system build.
5. Intervene only when desired or when the system reaches a true decision boundary.

Behind that simple interface, Draupnir Forge repeatedly performs the complete Mythic Engineering process:

**intent -> discovery -> constraints -> architecture -> roadmap -> bounded task -> implementation -> review -> testing -> verification -> documentation -> re-grounding -> next task**

The apparent black box is therefore not an uncontrolled agent. It is a **structured recursive engineering machine**.

---

# 2. Why the Name "Draupnir Forge"

In Norse tradition, **Draupnir** is Odin's ring that repeatedly produces additional rings.

That makes it a useful metaphor for the product:

- one human intention enters;
- structured AI labor multiplies beneath it;
- each completed cycle produces more durable system structure;
- the result compounds without requiring the human to manually perform every implementation step.

**Forge** identifies the system as part of the Mythic Engineering family and emphasizes deliberate construction rather than uncontrolled generation.

The name also avoids reducing the project to "vibe coding." The intended system is closer to an **autonomous software-development forge governed by explicit architecture**.

---

# 3. Product Thesis

Modern coding models can generate enormous amounts of implementation knowledge and code, but raw generation does not solve the hardest problems of complex software development.

The central problems are:

- preserving intent over long development runs;
- keeping architecture coherent;
- preventing domain contamination;
- detecting model hallucinations about the repository;
- remembering previous decisions;
- keeping interfaces and invariants stable;
- testing actual runtime behavior;
- identifying when the current plan is wrong;
- updating documentation as the system evolves;
- recovering cleanly after interrupted sessions;
- deciding when a human judgment call is truly needed.

Draupnir Forge exists to automate these disciplines.

The system assumes that **complex software can exceed what a single human can reliably retain in active memory**. Therefore the complete design must not live only inside either the human mind or one model context window.

The system externalizes project cognition into durable artifacts:

- vision;
- architecture;
- domain maps;
- interfaces;
- invariants;
- roadmaps;
- task state;
- decisions;
- known failures;
- tests;
- runtime evidence;
- change history;
- model findings;
- unresolved questions.

The human remains the source of **purpose, direction, judgment, preference, and final authority**.

The AI system carries a large portion of **retrieval, decomposition, implementation, analysis, testing, review, documentation, and operational memory**.

---

# 4. Primary Design Principle

## Own the Definition, Delegate the Construction

The user does not need to personally retain every API, syntax rule, dependency behavior, algorithm, file relationship, or implementation detail.

The user must retain or deliberately define enough of the following to govern the project:

- what should exist;
- why it should exist;
- what outcomes matter;
- what constraints matter;
- what must never happen;
- what tradeoffs are acceptable;
- what counts as success;
- when the system's interpretation is wrong.

Draupnir Forge turns those human-level definitions into progressively more explicit machine-operable structure.

Its deepest job is therefore not "write code."

Its deepest job is:

> **Translate human intent into an externally represented engineering reality that AI agents can continuously build, test, audit, and refine.**

---

# 5. User Experience Philosophy

The default interface should feel much simpler than the machinery underneath it.

## 5.1 Default Mode: Black-Box Forge

The normal user sees:

- a project goal field;
- optional files/repository attachment;
- an interpreted project summary;
- a progress view;
- a current activity statement;
- major decisions requiring approval;
- test and health status;
- pause / resume / stop controls;
- final results.

The user should **not** have to manually orchestrate agents, write elaborate prompts, maintain task files, choose every model, or repeatedly authorize obvious next steps.

## 5.2 Expert Mode: Open the Forge

Advanced users can reveal:

- architecture documents;
- agent-role outputs;
- roadmap;
- task graph;
- domain map;
- invariants;
- model routing;
- token budgets;
- test results;
- diffs;
- decision ledger;
- confidence estimates;
- unresolved contradictions;
- live event stream.

The product is therefore **black-box by default, glass-box on demand**.

## 5.3 Human Interruption Must Be Cheap

At any time the human should be able to say:

- "Stop doing that."
- "Change the goal."
- "Do not touch this subsystem."
- "Use a different approach."
- "Explain why you chose this."
- "Undo the last architectural decision."
- "Continue."
- "Finish without asking me unless blocked."

The system must incorporate the instruction into the durable project state rather than treating it as ephemeral chat context.

---

# 6. Core Mythic Engineering Loop

Every substantial unit of work passes through a recursive cycle.

## Phase 1: Intent

Determine what the human is actually trying to create.

Outputs:

- `SYSTEM_VISION.md`
- current goal statement;
- user priorities;
- explicit non-goals;
- success definition.

## Phase 2: Discovery

Inspect the actual environment before proposing major work.

For an existing repository:

- map files;
- identify languages and frameworks;
- inspect tests;
- identify entry points;
- map dependencies;
- identify major domains;
- detect documentation;
- inspect runtime/build state.

For a new project:

- identify required capabilities;
- research appropriate existing technologies;
- distinguish reusable primitives from custom invention;
- identify likely risks.

Output:

- `DISCOVERY_REPORT.md`
- `DOMAIN_MAP.md`

## Phase 3: Constraints

Extract and formalize:

- platform constraints;
- performance targets;
- compatibility requirements;
- licensing boundaries;
- security constraints;
- user preferences;
- forbidden approaches;
- resource limits;
- deployment environment.

Output:

- `CONSTRAINTS.md`

## Phase 4: Architecture

Define:

- domains;
- subsystem ownership;
- interfaces;
- data flows;
- state ownership;
- dependency direction;
- failure boundaries;
- invariants.

Outputs:

- `ARCHITECTURE.md`
- `INTERFACES.md`
- `INVARIANTS.md`

## Phase 5: Roadmap

Transform architecture into an ordered dependency-aware build plan.

Each roadmap item must define:

- goal;
- affected domain;
- dependencies;
- acceptance criteria;
- tests;
- forbidden collateral changes;
- completion evidence.

Output:

- `ROADMAP.md`
- machine-readable task graph.

## Phase 6: Build

Select the next bounded task and delegate implementation.

The builder receives only the relevant context plus access to retrieve additional context when needed.

The builder must not redefine project architecture silently.

## Phase 7: Review

A separate review pass examines:

- architectural alignment;
- code quality;
- regression risk;
- interface violations;
- hidden assumptions;
- duplicated logic;
- unnecessary complexity;
- security or reliability concerns.

## Phase 8: Test

Run the strongest applicable tests:

- unit tests;
- integration tests;
- build;
- lint/static analysis;
- runtime probes;
- deterministic fixtures;
- compatibility tests;
- performance tests where relevant.

## Phase 9: Verify

Verification asks a different question from testing:

> Did we actually accomplish the intended behavior?

The verifier compares:

- original goal;
- task acceptance criteria;
- implementation;
- runtime evidence;
- test results;
- architectural invariants.

A technically passing change that fails the intended outcome is rejected.

## Phase 10: Document

Update durable project cognition:

- changed interfaces;
- new decisions;
- known limitations;
- architecture changes;
- task status;
- runtime findings;
- unresolved issues.

## Phase 11: Re-ground

Before beginning the next major task, re-read the current system truth.

Ask:

- What exists now?
- What changed?
- Is the roadmap still correct?
- Did implementation reveal false assumptions?
- Is architecture still coherent?
- Is the next task still the highest-value valid task?

Then loop.

---

# 7. Agent Roles

The user should not need to manually invoke these roles. They are internal cognitive modes or separate agents.

## 7.1 Skald - Intent Interpreter

Responsibilities:

- translate natural-language wishes into explicit goals;
- preserve the user's language and priorities;
- identify ambiguity;
- prevent technical interpretation from erasing the original vision.

The Skald does not design implementation.

## 7.2 Cartographer - Repository and System Mapper

Responsibilities:

- inspect actual repository state;
- create file and domain maps;
- identify dependency relationships;
- identify hotspots;
- detect discrepancies between documentation and reality.

The Cartographer reports what exists, not what ought to exist.

## 7.3 Architect - System Designer

Responsibilities:

- domain decomposition;
- interface design;
- ownership boundaries;
- dependency direction;
- invariant definition;
- technical tradeoff analysis;
- architecture updates when evidence requires them.

## 7.4 Planner - Roadmap Compiler

Responsibilities:

- convert architecture into dependency-aware work units;
- size tasks for reliable model execution;
- define acceptance criteria;
- determine test strategy;
- schedule prerequisite work.

## 7.5 Forge Worker - Implementation Agent

Responsibilities:

- write and modify code;
- implement bounded tasks;
- create tests;
- perform mechanical refactors;
- follow architecture and task constraints.

The Forge Worker does not have authority to silently rewrite the project vision.

## 7.6 Auditor - Adversarial Reviewer

Responsibilities:

- search for bugs;
- find contradictions;
- identify edge cases;
- challenge assumptions;
- detect architecture drift;
- detect model-generated fiction about the codebase;
- identify unsafe or brittle behavior.

## 7.7 Tester - Runtime Reality Agent

Responsibilities:

- execute tests;
- inspect actual failures;
- reproduce bugs;
- generate targeted test cases;
- compare expected and observed behavior.

The Tester privileges runtime evidence over plausible prose.

## 7.8 Verifier - Acceptance Judge

Responsibilities:

- determine whether the work satisfies the actual goal;
- compare results against acceptance criteria and invariants;
- reject "technically complete" work that does not produce the required behavior.

## 7.9 Scribe - Operational Memory Agent

Responsibilities:

- maintain living documentation;
- record decisions;
- update architecture;
- compress completed work;
- preserve continuity across sessions;
- ensure future agents inherit accurate context.

## 7.10 Heimdallr - Boundary and Health Monitor

Optional system-level role.

Responsibilities:

- watch process health;
- detect hung agents;
- detect repeated failures;
- watch token and compute budgets;
- detect runaway loops;
- trigger recovery or escalation.

---

# 8. The Orchestrator

The **Forge Orchestrator** is the heart of Draupnir Forge.

It is not merely another coding agent.

It manages:

- project state;
- agent roles;
- task scheduling;
- dependency graph;
- context assembly;
- model routing;
- retries;
- verification gates;
- token budgets;
- interruption;
- recovery;
- escalation to the user.

The orchestrator should be deterministic wherever practical.

LLMs may recommend transitions, but a structured state machine should govern which transitions are legal.

Example:

```text
INTAKE
  -> DISCOVERY
  -> DEFINITION
  -> ARCHITECTURE
  -> ROADMAP
  -> TASK_READY
  -> IMPLEMENTING
  -> REVIEWING
  -> TESTING
  -> VERIFYING
      -> COMPLETE_TASK
      -> REPAIR
      -> REPLAN
      -> HUMAN_DECISION
  -> DOCUMENTING
  -> REGROUNDING
  -> TASK_READY
  -> ...
  -> PROJECT_COMPLETE
```

---

# 9. Project Memory Architecture

The application should assume that neither a human nor one LLM context window can contain the whole project.

Project cognition must be external.

## 9.1 Canonical Memory

Durable high-authority documents:

```text
.mythis/
  SYSTEM_VISION.md
  ARCHITECTURE.md
  DOMAIN_MAP.md
  INTERFACES.md
  INVARIANTS.md
  CONSTRAINTS.md
  ROADMAP.md
  DECISIONS.md
  KNOWN_ISSUES.md
  CAPABILITY_LEDGER.md
  PROJECT_STATE.json
```

The `.mythis/` name is provisional and can be changed.

## 9.2 Evidence Memory

Machine-generated evidence:

```text
.mythis/evidence/
  tests/
  runtime/
  benchmarks/
  screenshots/
  logs/
  audits/
  diffs/
```

## 9.3 Session Memory

Short-term execution records:

```text
.mythis/sessions/
  YYYY-MM-DD-session-id/
    goal.md
    actions.jsonl
    findings.md
    result.md
```

## 9.4 Decision Ledger

Every important architectural choice should record:

- decision;
- reason;
- alternatives considered;
- evidence;
- consequences;
- date;
- responsible role;
- conditions that would justify revisiting it.

This prevents future agents from "rediscovering" and reversing old decisions without understanding why they existed.

---

# 10. Context Compiler

A major component should be a **Context Compiler**.

Its job is to generate the smallest sufficient context package for each agent invocation.

It pulls from:

- system vision;
- current task;
- relevant domain docs;
- relevant interfaces;
- relevant invariants;
- selected source files;
- previous failure evidence;
- applicable decisions.

It deliberately avoids dumping the entire repository into every prompt.

This reduces:

- token waste;
- distraction;
- contradictory context;
- accidental cross-domain changes.

The Context Compiler is one of the primary mechanisms that allows Draupnir Forge to scale beyond one context window.

---

# 11. Roadmap Compiler

The roadmap is not just prose.

Internally it should be represented as a task graph.

Example conceptual schema:

```json
{
  "task_id": "T-042",
  "title": "Implement persistent model registry",
  "domain": "model-management",
  "status": "ready",
  "depends_on": ["T-038", "T-040"],
  "goal": "Persist verified model metadata across restarts",
  "constraints": [
    "Do not change public API behavior",
    "SQLite remains the storage backend"
  ],
  "acceptance": [
    "Registry survives process restart",
    "Duplicate model records are rejected",
    "Existing registry tests remain green"
  ],
  "verification": [
    "integration_test:model_registry_restart",
    "unit_test:model_registry_duplicates"
  ]
}
```

Tasks should be dynamically split when they become too broad.

A task that repeatedly fails is evidence that:

- it is too large;
- its assumptions are wrong;
- architecture is incomplete;
- dependencies are missing;
- the selected model is unsuitable.

The solution is not unlimited blind retrying.

---

# 12. Autonomous Loop Logic

Pseudo-process:

```text
while project_not_complete:

    load_canonical_project_state()

    if project_state_invalid_or_stale:
        run_cartographer()
        reconcile_docs_with_reality()

    task = roadmap.next_ready_task()

    if task.requires_human_judgment:
        escalate(task)
        wait_for_user_decision()
        persist_decision()
        continue

    context = context_compiler.build(task)

    implementation = forge_worker.execute(context)

    review = auditor.review(task, implementation)

    if review.requires_repair:
        create_bounded_repair_task()
        continue

    test_result = tester.run(task)

    if test_result.failed:
        classify_failure()
        create_repair_or_replan_task()
        continue

    verification = verifier.evaluate(task)

    if verification.failed:
        diagnose_goal_gap()
        repair_or_replan()
        continue

    scribe.update_project_memory()
    mark_task_complete()

    reground_project()

    if roadmap_invalid:
        architect_and_planner.revise()

run_final_project_verification()
```

---

# 13. Human Escalation Policy

The system should aggressively avoid unnecessary permission loops.

It should ask the human only when one of the following is true:

- two or more materially different valid directions exist and preference matters;
- the requested behavior conflicts with an existing explicit user requirement;
- a destructive action exceeds previously granted authority;
- legal, financial, security, privacy, or deployment consequences require human authorization;
- required credentials or external access are unavailable;
- the project goal itself is ambiguous;
- a major architectural change would invalidate an explicit user decision;
- repeated autonomous attempts cannot resolve a blocker.

The system should **not** ask:

- whether it may continue to the next roadmap item;
- whether it may fix an obvious test failure caused by its own change;
- whether it may update documentation after changing architecture;
- whether it may inspect files necessary to complete an already authorized task;
- whether it should perform verification that is already part of the workflow.

---

# 14. Model Routing

Draupnir Forge should be model-agnostic.

A routing layer can select models based on task type.

Example categories:

- high-reasoning model for architecture;
- coding-specialized model for implementation;
- inexpensive model for repository summarization;
- long-context model for broad audits;
- local model for privacy-sensitive tasks;
- vision model for UI/screenshot verification.

The system should support:

- OpenAI-compatible APIs;
- local inference;
- Ollama-compatible endpoints;
- configurable providers;
- fallback models;
- per-role model preferences;
- per-project cost ceilings.

No project architecture should depend unnecessarily on one model vendor.

---

# 15. Token and Compute Economy

The product should treat tokens as a managed engineering resource.

Mechanisms:

- bounded context assembly;
- retrieval instead of full-context repetition;
- summary layers;
- durable state outside prompts;
- cheap-model preprocessing;
- expensive-model escalation only when useful;
- role-specific prompts;
- diff-focused review;
- incremental repository maps;
- caching stable analyses;
- task-size control.

The goal is not minimum token use at all costs.

The goal is:

> **Maximum verified engineering progress per unit of inference.**

---

# 16. Failure Classification

Every failure should be classified rather than merely retried.

Suggested classes:

- `IMPLEMENTATION_ERROR`
- `TEST_FAILURE`
- `ARCHITECTURE_MISMATCH`
- `CONTEXT_MISSING`
- `FALSE_ASSUMPTION`
- `DEPENDENCY_FAILURE`
- `ENVIRONMENT_FAILURE`
- `MODEL_LIMITATION`
- `TASK_TOO_LARGE`
- `AMBIGUOUS_REQUIREMENT`
- `USER_DECISION_REQUIRED`
- `RESOURCE_LIMIT`
- `SECURITY_BLOCK`
- `UNKNOWN`

Repeated failures of the same class should trigger escalation to a higher-level role.

Example:

```text
implementation fails twice
-> Auditor diagnosis
-> Architect review
-> Roadmap revision
-> model switch if appropriate
-> human escalation only if unresolved
```

---

# 17. Verification Gates

A task is not complete because the Forge Worker says it is complete.

Completion requires evidence.

Recommended gates:

1. **Code gate** - expected files or changes exist.
2. **Build gate** - project builds where applicable.
3. **Test gate** - required tests pass.
4. **Interface gate** - public contracts remain valid.
5. **Invariant gate** - system truths remain true.
6. **Runtime gate** - behavior is observed, not merely predicted.
7. **Goal gate** - acceptance criteria are actually satisfied.
8. **Documentation gate** - durable project state reflects reality.

Only then can a task become `DONE`.

---

# 18. Architecture Drift Detection

The system should regularly compare:

- architecture documentation;
- repository topology;
- imports/dependencies;
- interface definitions;
- runtime behavior;
- roadmap assumptions.

Drift examples:

- domain logic appearing in the wrong subsystem;
- a "temporary" compatibility layer becoming permanent;
- duplicated ownership;
- hidden global state;
- interfaces changing without documentation;
- roadmap tasks assuming components that no longer exist.

Significant drift triggers a re-grounding cycle.

---

# 19. Black-Box Progress Interface

The default UI should expose useful progress without flooding the user with internals.

Example:

```text
DRAUPNIR FORGE

Project: Local Research Assistant

Overall: 63%

Current objective:
Building citation-preserving retrieval pipeline

Current phase:
Verification

Completed:
✓ Project definition
✓ Repository architecture
✓ File ingestion
✓ Markdown parser
✓ Search index
✓ Retrieval API

Now:
◉ Verifying citation locations against source documents

Queued:
○ Answer synthesis
○ UI
○ Final integration tests

Human decisions needed:
None

System health:
Stable
```

The interface can optionally expose a live "forge stream" for users who enjoy watching agent activity.

---

# 20. User Control Levels

Suggested autonomy presets:

## Guided

Ask before:

- architecture changes;
- new dependencies;
- destructive refactors;
- external services;
- deployment.

## Trusted

Continue autonomously inside agreed architecture.

Ask only for:

- preference decisions;
- irreversible actions;
- new external commitments;
- major goal changes.

## Deep Forge

Continue until:

- project complete;
- resource budget reached;
- hard blocker;
- human-required decision.

This is the mode closest to "give the model a roadmap and let it work until the tokens run out."

---

# 21. Clean-Room Capability

Draupnir Forge should support a clean-room research mode for building systems inspired by existing tools without simply copying their implementation.

The system may study, as legally and ethically appropriate:

- public documentation;
- public API behavior;
- file formats;
- protocol specifications;
- command-line behavior;
- compatibility requirements;
- published benchmarks;
- publicly observable behavior;
- properly licensed open-source primitives.

It then creates an independent:

- requirements model;
- compatibility map;
- architecture;
- roadmap;
- implementation.

The project should preserve provenance and licensing information for anything reused directly.

---

# 22. Existing-System Mode

The system must be equally capable of entering an existing repository.

Initial sequence:

```text
SCAN
-> MAP
-> BUILD DOMAIN MODEL
-> DETECT TESTS
-> DETECT ARCHITECTURE
-> COMPARE DOCS TO CODE
-> IDENTIFY RISKS
-> CONFIRM USER GOAL
-> CREATE CHANGE ROADMAP
-> EXECUTE
```

The first rule of an unfamiliar repository:

> **Map before modifying.**

---

# 23. New-System Mode

For a new repository:

```text
INTENT
-> REQUIREMENTS
-> TECHNOLOGY RESEARCH
-> CONSTRAINTS
-> ARCHITECTURE
-> SCAFFOLD
-> FIRST THIN VERTICAL SLICE
-> VERIFY
-> EXPAND ITERATIVELY
```

The system should resist generating an enormous speculative codebase before reality has tested the architecture.

---

# 24. Thin Vertical Slice Rule

New capabilities should preferably be proven end-to-end before broad expansion.

Example:

Instead of implementing:

- complete database;
- complete API;
- complete model layer;
- complete UI;

independently and hoping they integrate later,

first prove:

```text
one real user action
-> UI/input
-> API
-> state
-> model/service
-> persistence
-> returned result
-> test
```

Then expand.

This gives architectural evidence early.

---

# 25. Self-Repair Without Self-Deception

The system may automatically repair failures, but repairs must remain evidence-driven.

Bad pattern:

```text
test failed
-> change test until green
```

Required reasoning:

```text
test failed
-> determine whether implementation, test, requirement, or architecture is wrong
-> preserve original evidence
-> justify change
-> rerun verification
```

Tests are not obstacles to completion. They are witnesses.

---

# 26. Completion Definition

A project reaches `PROJECT_COMPLETE` only when:

- all mandatory roadmap capabilities are complete;
- all required verification gates pass;
- no unresolved critical issues remain;
- architecture and implementation agree sufficiently;
- documentation reflects actual state;
- the application can be built/run using documented steps;
- user-defined acceptance criteria are satisfied.

The system then generates:

```text
FINAL_REPORT.md
```

containing:

- what was built;
- architecture summary;
- how to run it;
- verification performed;
- remaining limitations;
- future roadmap ideas;
- known risks.

---

# 27. MVP Scope

The first useful Draupnir Forge should **not** attempt everything above at once.

## MVP must have

- CLI-first interface;
- new-project and existing-repository modes;
- one primary LLM provider;
- project-state directory;
- vision extraction;
- repository mapping;
- architecture generation;
- roadmap generation;
- bounded task loop;
- code modification;
- command execution;
- test loop;
- auditor pass;
- living Markdown updates;
- pause/resume;
- Git checkpoints;
- human escalation;
- token/cost budget.

## MVP can postpone

- graphical interface;
- many-provider routing;
- distributed agents;
- remote workers;
- vision/UI testing;
- formal sandboxing;
- business workflow support;
- plugin marketplace;
- team collaboration;
- autonomous deployment.

---

# 28. Suggested Technical Shape

This is intentionally not a final stack decision.

Potential implementation:

```text
CLI / TUI
   |
Forge Orchestrator
   |
State Machine
   |
+------------------------------+
| Context Compiler             |
| Roadmap / Task Graph         |
| Project Memory               |
| Model Router                 |
| Tool Executor                |
| Verification Engine          |
| Event Log                    |
+------------------------------+
   |
AI Roles
   |
Repository / Shell / Tests / Git
```

Likely useful technologies:

- Python for initial orchestration speed;
- SQLite for durable task/event/decision state;
- Markdown for human-readable architecture;
- JSON/JSONL for machine state and event history;
- Git for checkpoints and rollback;
- OpenAI-compatible model abstraction;
- subprocess/tool sandbox;
- optional Unix-domain-socket/event-bus integration later.

A future implementation could integrate naturally with Verðandi-style event infrastructure.

---

# 29. Event Model

Internally, important actions should emit structured events.

Examples:

```text
PROJECT_CREATED
VISION_UPDATED
DOMAIN_DISCOVERED
ARCHITECTURE_UPDATED
ROADMAP_REVISED
TASK_STARTED
TASK_FAILED
TASK_REPAIRED
TEST_FAILED
TEST_PASSED
INVARIANT_VIOLATED
HUMAN_DECISION_REQUESTED
HUMAN_DECISION_RECEIVED
MODEL_SWITCHED
CHECKPOINT_CREATED
TASK_COMPLETED
PROJECT_REGROUNDED
PROJECT_COMPLETED
```

Events make the system observable and later allow:

- analytics;
- debugging;
- replay;
- multi-agent coordination;
- self-improvement research;
- failure-pattern discovery.

---

# 30. Git Strategy

Every meaningful successful unit of work should create a checkpoint.

Recommended pattern:

```text
checkpoint before risky change
-> implementation
-> verification
-> commit on success
-> rollback or repair on failure
```

Commit metadata can include:

- task ID;
- agent role;
- acceptance result;
- verification summary.

The application should never rely on "the model probably remembers what it changed."

---

# 31. Security and Tool Authority

Agents should receive only the authority required for the current project.

Permission categories:

- repository read;
- repository write;
- command execution;
- package installation;
- network research;
- credential use;
- deployment;
- destructive file operations;
- external account actions.

Authority should be configurable per project.

Dangerous external actions should never be inferred merely from permission to write code.

---

# 32. Non-Goals

Draupnir Forge is **not** intended to be:

- an uncontrolled "AI writes an entire app from one prompt" toy;
- a replacement for verification;
- a claim that LLM outputs are inherently correct;
- a requirement that users understand every implementation detail;
- a requirement that one enormous model contain the entire project;
- a vendor-locked coding assistant;
- a system that hides failures to preserve the illusion of autonomy.

The apparent simplicity of the user interface must come from **automation of discipline**, not removal of discipline.

---

# 33. Success Metrics

The research value of the project can be measured.

Useful metrics include:

- percentage of roadmap tasks completed without human intervention;
- human interventions per development hour;
- token cost per verified task;
- average autonomous run length;
- regression rate;
- architecture-drift frequency;
- failed-attempt recovery rate;
- percentage of failures correctly classified;
- test pass durability after later changes;
- documentation accuracy;
- time from high-level request to verified working capability;
- ratio of user-direction time to total engineering work.

A particularly important metric:

> **Verified engineering output per minute of required human attention.**

That measures the real goal better than lines of generated code.

---

# 34. Long-Term Vision

Draupnir Forge begins as a software-development orchestrator, but the underlying architecture is domain-general.

The deeper pattern is:

```text
human intent
-> domain model
-> explicit constraints
-> structured architecture
-> delegated specialist cognition
-> execution
-> evidence
-> verification
-> persistent organizational memory
-> recursive improvement
```

Software engineering is the first domain because it provides unusually strong machine-verifiable evidence through code, tests, builds, logs, and runtime behavior.

Later descendants could potentially orchestrate:

- research projects;
- publishing pipelines;
- data analysis;
- content production;
- digital operations;
- business processes;
- autonomous agent ecosystems.

The transferable invention is not merely an automated coder.

It is a framework for **one human directing complexity that exceeds what one human can personally retain or execute**.

---

# 35. Future Extensions

Possible later expansions:

- multi-repository projects;
- cloud execution workers;
- local/private model pools;
- dynamic specialist-agent creation;
- learned routing from historical outcomes;
- automated benchmark generation;
- UI screenshot verification;
- dependency vulnerability auditing;
- issue tracker integration;
- pull-request generation;
- human-team mode;
- autonomous documentation sites;
- plugin/tool marketplace;
- reusable architecture templates;
- domain packs;
- organizational/business orchestration;
- project-to-project knowledge transfer;
- distributed Verðandi event backbone;
- Muninn-compatible long-term memory;
- Skuld-style obligation and verification ledger.

---

# 36. Repository Seed Structure

```text
RuneForgeAI-Draupnir-Forge/
├── README.md
├── LICENSE
├── DRAUPNIR_FORGE_SPEC.md
├── MYTHIC_ENGINEERING.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DOMAIN_MAP.md
│   ├── EVENT_MODEL.md
│   ├── SECURITY_MODEL.md
│   └── ROADMAP.md
├── src/
│   ├── orchestrator/
│   ├── roles/
│   ├── context/
│   ├── memory/
│   ├── roadmap/
│   ├── verification/
│   ├── models/
│   ├── tools/
│   └── events/
├── tests/
└── examples/
```

---

# 37. First Prototype Milestone

The first prototype succeeds if a user can:

1. point Draupnir Forge at a small existing repository;
2. describe one meaningful feature;
3. allow the system to map the repository;
4. generate/update architecture notes;
5. create a bounded roadmap;
6. implement the feature;
7. run tests;
8. autonomously repair at least one induced failure;
9. verify the requested behavior;
10. update documentation;
11. produce a Git checkpoint;
12. report completion without requiring continuous user direction.

That single vertical slice would prove the central concept.

---

# 38. Foundational Laws of the Forge

1. **Human intent outranks model convenience.**
2. **Reality outranks generated explanation.**
3. **Architecture outranks patch accumulation.**
4. **Definition precedes delegation.**
5. **No critical system truth should exist only in one mind or one context window.**
6. **Every subsystem must have an owner and boundary.**
7. **Every important change must leave evidence.**
8. **A model may implement its own work, but it may not be the sole judge of that work.**
9. **Failure should produce knowledge, not blind repetition.**
10. **The system should ask the human for judgment, not for permission to perform obvious engineering work.**
11. **Continuity is a first-class engineering requirement.**
12. **The simpler the surface becomes, the more disciplined the hidden machinery must be.**

---

# 39. One-Sentence Definition

> **Draupnir Forge is a black-box-by-default, glass-box-on-demand autonomous development system that recursively applies Mythic Engineering so one human can direct software complexity far beyond what that human could manually implement or retain in working memory.**

---

# 40. Short GitHub Description

**Recursive Mythic Engineering for autonomous vibe coding: define the vision, then let a disciplined AI forge map, architect, build, test, audit, document, and re-ground the project until it works.**

---

# 41. Tagline

> **One intent. A thousand hammer blows. One coherent system.**

---

# 42. Alternate Names

If `Draupnir Forge` is ever undesirable, strong alternatives include:

- **Skuld Forge** - emphasizes future work, obligation, verification, and completion.
- **WyrdForge** - emphasizes interconnected state and consequence.
- **Völundr Loop** - emphasizes master craftsmanship and recursive creation.
- **Mythic Foundry** - direct but less distinctly Norse.
- **ForgeCycle** - technically clear and easy to understand.
- **RuneForge Autopilot** - very explicit, but less distinctive.
- **Draupnir Engine** - emphasizes recursive multiplication more than craftsmanship.

Current preferred name: **RuneForgeAI - Draupnir Forge**.
