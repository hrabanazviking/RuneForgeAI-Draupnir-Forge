# Draupnir Forge — Domain Map

*Slice 43 canonical doc. Every module in `src/draupnir_forge/`,
grouped by the domain it serves. Hand-checked against the source.*

## Interface — the Forge's face

| Module | Serves |
|---|---|
| `__main__.py` | `python -m draupnir_forge` entry; delegates to `cli.main` |
| `cli.py` | argparse CLI: `init`, `forge`, `status`, `roadmap`, `events`, `architecture`, `decisions`, `checkpoint`, `metrics`; exit 0 ok / 2 usage |
| `ui.py` | Black-box progress panel (`ProgressView.render`): % complete, now/queued, health — pure function of state |

## Configuration — the Forge's memory of its own rules

| Module | Serves |
|---|---|
| `config.py` | `ForgeConfig`: 5-layer merge (defaults < `~/.draupnir/config.yaml` < `.mythis/config.yaml` < `DRAUPNIR_*` env < explicit), validated against `data/config_schema.yaml` |
| `_paths.py` | Data-file resolution: `DRAUPNIR_DATA_DIR` → `importlib.resources` → walk-up to repo `data/` → `./data`; never raises |
| `data/*.yaml` | All settings: defaults, schema, role models, prices, prompts, checklists, transitions of intent; a packaged copy lives in `src/draupnir_forge/data/` (slice 48) so installed wheels resolve data via `importlib.resources` |

## Foundation — the Forge's bones

| Module | Serves |
|---|---|
| `log.py` | `get_logger`, `ForgeFormatter`, console + rotating file handler under `.mythis/logs/` |
| `events.py` | `EventType` (18), `ForgeEvent`, `EventLog`: append-only JSONL, monotonic seq, crash-safe (flush+fsync) |
| `state.py` | `ProjectState`: persisted phase in `PROJECT_STATE.json`; `ALLOWED_TRANSITIONS`; corrupt file → backup + fresh |
| `budget.py` | Token/cost caps from `data/model_prices.yaml`; `BudgetExhausted` on overrun; persisted to `.mythis/budget.json` |

## Governance — the Forge's law

| Module | Serves |
|---|---|
| `machine.py` | `ForgeMachine`: 17 states, validates every move against the transition table, emits state-change events, persists phase |
| `security.py` | `SecurityPolicy.authorize()`/`check_or_raise()`: config gate (`tools.allow_*`, INSTALL/NETWORK/DESTRUCTIVE denied by default) + role gate (`data/role_authority.yaml`); `Authority` IntEnum; audit to `.mythis/security_audit.jsonl` |
| `__init__.py` | `__version__`, `FORGE_LAWS` — the 12 laws every subsystem honors |

## Direction — the Forge's will

| Module | Serves |
|---|---|
| `orchestrator.py` | Core loop: drives the machine, dispatches roles per state, handles REPAIR/REPLAN/HUMAN_DECISION, emits lifecycle events |
| `roadmap.py` | `TaskGraph` runtime: `ready_tasks`, `mark_complete/failed`, `split_task`, dependency validation |
| `context.py` | `ContextCompiler`: builds the task-scoped `ContextPackage` (vision excerpt, domain files, interfaces, relevant decisions/failures) with token budget + truncation policy |
| `tasks.py` | Re-export of `ForgeTask` (canonical home: `roles/planner.py`) |

## Intelligence — the ten roles (`roles/`)

| Module | Role | Serves |
|---|---|---|
| `base.py` | — | `Role` ABC, `RoleContext`, `RoleResult`, `RoleRegistry` |
| `skald.py` | Skald | Intent → `Vision` (goal, priorities, non-goals, success criteria, ambiguities); rule-based v1 + YAML prompts |
| `cartographer.py` | Cartographer | Repo walk → `DOMAIN_MAP.md` + machine map; AST import graph |
| `architect.py` | Architect | Vision + domain map → `ARCHITECTURE.md`, `INTERFACES.md`, `INVARIANTS.md` + `architecture.json` |
| `planner.py` | Planner | Architecture + vision → task graph (`T-001…`), topological sort, cycle detection |
| `worker.py` | ForgeWorker | One bounded task: `apply_patch`, shell via `ToolExecutor`, evidence to `.mythis/evidence/diffs/`; refuses out-of-project and `.mythis/` paths |
| `auditor.py` | Auditor | Diff review vs `data/audit_checks.yaml`: secrets, bare `except`, `print(` in lib code, TODOs, complexity, duplication, arch drift |
| `tester.py` | Tester | Discovers and runs tests, parses counts, classifies failures; never edits tests |
| `verifier.py` | Verifier | 8-gate acceptance verdict (`Verifier.evaluate`) |
| `scribe.py` | Scribe | Owns `.mythis/` canonical docs; append-only merges (decisions, roadmap status, issues, capabilities) |
| `heimdallr.py` | Heimdallr | Loop health: 3× same-task failure, runaway, budget pressure → escalations |

## World access — the Forge's hands and voice

| Module | Serves |
|---|---|
| `models.py` | `ModelRouter`: multi-provider (openai/ollama/custom) OpenAI-compatible client over stdlib `urllib`; per-role models from `data/role_models.yaml`; retry/backoff; provider failover on repeated 5xx; charges `Budget` |
| `tools.py` | `ToolExecutor`: `run(cmd, cwd, timeout, allow_network)` under the authority model (`AuthorityDenied` on refusal); `apply_patch()` with path guards |
| `checkpoints.py` | `Checkpointer`: git checkpoints with `Forge-Task:` trailers, `rollback`, `pause`/`resume` |

## Quality — the Forge's conscience

| Module | Serves |
|---|---|
| `verification.py` | `VerificationEngine`: the 8 gates as functions (`code`, `build`, `test`, `interface`, `invariant`, `runtime`, `goal`, `documentation`); one failing gate blocks DONE |
| `failures.py` | `FailureClass` (14 classes), `classify_failure` (regex + exception mapping), escalation ladder (2× Auditor, 3× Architect, 4× human) |
| `drift.py` | `DriftDetector`: `architecture.json` vs live repo (cross-domain imports, new dirs, interface changes) → `KNOWN_ISSUES.md` + REGROUNDING |
| `repair.py` | `RepairEngine`: classify → bounded repair diff → re-test → escalate; max 3 attempts, then REPLAN |
| `reground.py` | `reground()`: incremental re-map, stale-task detection, Architect revision; emits `PROJECT_REGROUNDED` |
| `metrics.py` | `Metrics(EventLog, Budget).compute()`: spec §33 success metrics (completions, intervention rate, tokens/verified-task, regression, recovery) + ASCII `render()` |

## Knowledge — the Forge's saga

| Module | Serves |
|---|---|
| `memory.py` | `ProjectMemory`: `.mythis/` skeleton, Scribe-gated `read_doc`/`write_doc`, `snapshot()` for the context compiler |
| `sessions.py` | `Session`: run goal/actions/findings/result under `.mythis/sessions/<id>/`; `replay()` returns a numbered debugging view, never re-executes |
| `report.py` | `FinalReport.generate()`: spec §26 markdown (built, architecture, how to run, verification, limitations, future, risks) → `.mythis/FINAL_REPORT.md` |
| `decisions.py` | `DecisionLedger`: `record` with reason/alternatives/evidence/consequences; contradiction warnings on re-litigation |
| `discovery.py` | `DiscoveryReport`: cartographer output + test/doc inventory + risks → `DISCOVERY_REPORT.md` |
| `archdocs.py` | Pure renderers: `architecture.json` → `ARCHITECTURE.md` (mermaid), `INTERFACES.md` tables, `INVARIANTS.md` checklist |

## Modes — the two doors in

| Module | Serves |
|---|---|
| `modes_new.py` | Green-field: intent → requirements → constraints → architecture → scaffold → first thin slice |
| `modes_existing.py` | Existing repo: SCAN → MAP → risks → confirm goal → change roadmap; "map before modifying" enforced |

## Human — the Jarl's seat

| Module | Serves |
|---|---|
| `escalation.py` | `EscalationPolicy.needs_human` (§13 ask-conditions as predicates); `.mythis/awaiting_human.md` pause file; `--answer` resume |

## Dependency direction (allowed)

```
cli → orchestrator → machine → state
orchestrator → roles/* → {models, tools, events, budget, context}
roles/worker → tools          roles/tester → failures
orchestrator → {roadmap, memory, escalation, checkpoints, failures}
machine → {state, events}     verification → roles/verifier (concept)
* → {config, log, _paths}     (foundation; everyone may use)
```

Nothing imports `cli` (entry only). Roles never import each other;
the orchestrator and registry compose them. `tasks.py` re-exports from
`roles/planner.py` — the single canonical home of `ForgeTask`.
