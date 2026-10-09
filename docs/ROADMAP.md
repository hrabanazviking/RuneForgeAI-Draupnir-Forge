# Draupnir Forge — Build Roadmap: all 50 slices complete

*Forged 2026-10-09 during the coding surge. Every slice landed as
production code with green tests; the full suite is 709 tests.*

| # | Slice | Landed as |
|---|-------|-----------|
| 1 | Project scaffold | `src/draupnir_forge/`, `data/`, `tests/` layout |
| 2 | Configuration system | `config.py` — 5-layer merge, YAML schema validation |
| 3 | Logging infrastructure | `log.py` — console + rotating file under `.mythis/logs/` |
| 4 | CLI skeleton | `cli.py` — `draupnir` entry, honest exit codes |
| 5 | Event model + event log | `events.py` — 18 `EventType`s, append-only JSONL |
| 6 | Project state store | `state.py` — `ALLOWED_TRANSITIONS` as data, atomic writes |
| 7 | Failure classification | `failures.py` — 14 classes, escalation ladder |
| 8 | Token/cost budget tracker | `budget.py` — caps, `BudgetExhausted`, `.mythis/budget.json` |
| 9 | Role base class + registry | `roles/base.py` — `Role` ABC, `RoleContext`, `RoleResult` |
| 10 | Skald (intent interpreter) | `roles/skald.py` — intent → `Vision` |
| 11 | Cartographer (repository mapper) | `roles/cartographer.py` — AST import graph, domains |
| 12 | Architect (system designer) | `roles/architect.py` — ARCHITECTURE.md, INTERFACES.md, INVARIANTS.md |
| 13 | Planner (roadmap compiler) | `roles/planner.py` — task graph, topo sort, cycle detection |
| 14 | Forge Worker (implementation agent) | `roles/worker.py` — patch apply, bounded shell, evidence |
| 15 | Auditor (adversarial reviewer) | `roles/auditor.py` — diff review vs `audit_checks.yaml` |
| 16 | Tester (runtime reality agent) | `roles/tester.py` — discovers + runs suite, classifies failures |
| 17 | Verifier (acceptance judge) | `roles/verifier.py` — 8-gate verdict |
| 18 | Scribe + Heimdallr | `roles/scribe.py`, `roles/heimdallr.py` — docs + loop health |
| 19 | Forge state machine | `machine.py` — 17 states over the transition table |
| 20 | Forge Orchestrator core loop | `orchestrator.py` — drives the machine, dispatches roles |
| 21 | Context Compiler | `context.py` — task-scoped `ContextPackage` |
| 22 | Roadmap compiler (task graph) | `roadmap.py` — `TaskGraph` runtime |
| 23 | Model router | `models.py` — OpenAI-compatible client over stdlib `urllib` |
| 24 | Tool executor | `tools.py` — authority-gated shell + `apply_patch` |
| 25 | Verification engine (8 gates) | `verification.py` — gates as functions |
| 26 | Project memory | `memory.py` — `.mythis/` skeleton, Scribe-gated docs |
| 27 | Human escalation engine | `escalation.py` — ask-conditions, pause file, `--answer` resume |
| 28 | Pause/resume + git checkpoints | `checkpoints.py` — `Forge-Task:` trailers, rollback |
| 29 | New-project mode | `modes_new.py` — intent → scaffold → first thin slice |
| 30 | Existing-repo mode | `modes_existing.py` — SCAN → MAP → change roadmap |
| 31 | Black-box progress CLI | `ui.py` — `ProgressView.render` |
| 32 | Glass-box inspection commands | `cli.py` — `status`, `events`, `architecture`, `decisions` |
| 33 | Discovery report generator | `discovery.py` — DISCOVERY_REPORT.md |
| 34 | Architecture document generator | `archdocs.py` — pure renderers |
| 35 | Decision ledger | `decisions.py` — contradiction warnings |
| 36 | Drift detection | `drift.py` — arch.json vs live repo → KNOWN_ISSUES.md |
| 37 | Self-repair engine | `repair.py` — classify → bounded fix → re-test → escalate |
| 38 | Re-grounding cycle | `reground.py` — incremental re-map, stale-task detection |
| 39 | Security model | `security.py` — `Authority`, `SecurityPolicy`, audit log |
| 40 | Clean-room research mode | `cleanroom.py` — default-deny research sandbox |
| 41 | Success metrics dashboard | `metrics.py` — spec §33 metrics + `draupnir metrics` |
| 42 | Session memory + replay | `sessions.py` — `.mythis/sessions/<id>/`, debug replay |
| 43 | Canonical docs | `docs/` — ARCHITECTURE, DOMAIN_MAP, EVENT_MODEL, SECURITY_MODEL |
| 44 | Multi-provider model support | `models.py` — provider registry + failover chain |
| 45 | Examples | `examples/hello-forge/` — goal, expected vision, README |
| 46 | Dogfood thin vertical slice | forge loop ran on a scratch repo; `events --since-time` landed |
| 47 | End-to-end integration test | `tests/test_e2e.py` — fixture repo → PROJECT_COMPLETE |
| 48 | Packaging & install docs | `pyproject.toml`, `docs/INSTALL.md`, packaged data |
| 49 | FINAL_REPORT generator | `report.py` — spec §26 report at PROJECT_COMPLETE |
| 50 | Release | README quickstart + this file + verified final push |

## Integration fixes found by dogfooding (slices 46–47)

Running the real loop end-to-end surfaced three integration gaps,
fixed additively:

1. **The run's goal never reached the Skald** — `_planning_step`
   now publishes `artifacts["goal_text"]`.
2. **Cartographer/Architect artifact mismatch** — the Architect
   accepts the Cartographer's `DomainMap` chart object.
3. **No `implementation` record for the Verifier** — the
   orchestrator synthesizes one from the Worker's `changed_files`
   and the Tester's `test_result`, so the goal gate has evidence.

The loop also wires completion rites: a git checkpoint per task
(`Forge-Task:` trailer), `FINAL_REPORT.md` at PROJECT_COMPLETE,
and a final checkpoint.

## How to verify

```bash
PYTHONPATH=src python3 -m unittest discover -s tests  # 709 green
git ls-remote origin HEAD                              # matches local HEAD
```
