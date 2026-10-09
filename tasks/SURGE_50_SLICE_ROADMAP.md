# SURGE 50-SLICE ROADMAP — RuneForgeAI-Draupnir-Forge

**Goal:** Build Draupnir Forge from pure spec (0 code) into a working,
production-quality autonomous Mythic Engineering development system.

**Spec sources:** `DRAUPNIR_FORGE_SPEC.md` (§1–§42), `README.md`,
`RULES.AI.md` (project laws).

**Stack:** Python 3.10+, stdlib + PyYAML only. `unittest` for tests
(no pytest dependency). Package layout per spec §36 (`src/` tree).

**Package:** `src/draupnir_forge/` · CLI: `draupnir` (pyproject entry point)

**Project laws (binding on every slice):**
- Real production code only. No pseudocode, no stubs, no TODO placeholders.
- Data/settings in YAML data files, never hardcoded.
- Modular, fault-tolerant (try/except at subsystem boundaries, never crash).
- Type hints, PEP 8, 4-space indents, docstrings with Norse flavor where apt.
- Additive fixes only. Push often. Tests green before moving on.

---

## WAVE 1 — FOUNDATION (slices 1–8)

### Slice 1 — Project scaffold
**Files:** `pyproject.toml`, `src/draupnir_forge/__init__.py`
(`__version__`, `FORGE_LAWS` list of the 12 laws), `src/draupnir_forge/__main__.py`,
`data/default_config.yaml`, `tests/__init__.py`, `.gitignore`
**Accept:** `pip install -e .` works; `python -m draupnir_forge --version`
prints version; `python -m unittest discover tests` runs (0 tests ok).

### Slice 2 — Configuration system
**Files:** `src/draupnir_forge/config.py`, `tests/test_config.py`
**Design:** `ForgeConfig` dataclass loaded from YAML with layered overrides
(defaults < `~/.draupnir/config.yaml` < project `.mythis/config.yaml` < env
`DRAUPNIR_*` < explicit dict). Keys: `model.provider`, `model.name`,
`model.api_base`, `budget.max_tokens`, `budget.max_cost_usd`,
`autonomy` (guided|trusted|deep), `git.auto_checkpoint` (bool).
Validation with clear errors; never crashes on missing file.
**Accept:** layered override test, missing-file test, invalid-value test green.

### Slice 3 — Logging infrastructure
**Files:** `src/draupnir_forge/log.py`, `tests/test_log.py`
**Design:** `get_logger(name)` → stdlib logging, ForgeFormatter
(`%(asctime)s [%(levelname)s] %(name)s: %(message)s`), console handler +
rotating file handler under project `.mythis/logs/forge.log`.
`bind_role(logger, role)` adds role field. No `print()` in library code.
**Accept:** log file created, role field present, no exception on unwritable dir.

### Slice 4 — CLI skeleton
**Files:** `src/draupnir_forge/cli.py`, `tests/test_cli.py`
**Design:** argparse with subcommands: `init`, `forge`, `status`, `roadmap`,
`events`, `checkpoint`, `version`. Each prints help and returns exit codes
(0 ok, 2 usage error). `init` delegates to modes (slice 29/30 later —
for now creates `.mythis/` skeleton). Global `--verbose/--quiet`,
`--project-dir`. All output via logging/print only in cli.py.
**Accept:** `--help` per subcommand works; unknown subcommand exits 2.

### Slice 5 — Event model + event log
**Files:** `src/draupnir_forge/events.py`, `tests/test_events.py`
**Design:** `EventType` enum with all §29 events (PROJECT_CREATED …
PROJECT_COMPLETED). `ForgeEvent` dataclass: `seq`, `ts` (UTC ISO),
`type`, `actor_role`, `project`, `payload` (dict), `caused_by` (seq|None).
`EventLog` appends JSONL to `.mythis/events.jsonl`, assigns monotonic seq,
`query(type=None, since=None, limit=None)`. Crash-safe append (flush+fsync).
**Accept:** 1000-event append/query round-trip; seq monotonic after reopen.

### Slice 6 — Project state store
**Files:** `src/draupnir_forge/state.py`, `tests/test_state.py`
**Design:** `ProjectState` manages `.mythis/PROJECT_STATE.json`:
`phase`, `goal`, `task_id`, `updated_ts`, `counters` dict.
Atomic writes (tmp+rename). `transition(new_phase)` validates against
allowed transitions map (§8 state machine list). Corrupt file →
backup + fresh state, never crash.
**Accept:** transition validation test; corrupt-file recovery test.

### Slice 7 — Failure classification
**Files:** `src/draupnir_forge/failures.py`, `tests/test_failures.py`
**Design:** `FailureClass` enum with all 14 §16 classes.
`classify_failure(exc=None, test_output="", context="") -> FailureClass`
using rule table (regexes on output + exception type mapping).
`FailureRecord` dataclass; escalation ladder:
2× same class → Auditor, 3× → Architect, 4× → human.
**Accept:** classification table unit tests for ≥10 classes.

### Slice 8 — Token/cost budget tracker
**Files:** `src/draupnir_forge/budget.py`, `tests/test_budget.py`
**Design:** `Budget` tracks `tokens_used`, `cost_usd` (per-model price table
in `data/model_prices.yaml`), `tasks_completed`. `charge(model, in_tok,
out_tok)`; `remaining()`; `exceeded()` → raises `BudgetExhausted`.
Persisted to `.mythis/budget.json`. `report()` → human-readable summary.
**Accept:** charge math test, exhaustion test, persistence round-trip.

---

## WAVE 2 — THE TEN ROLES (slices 9–18)

### Slice 9 — Role base class + registry
**Files:** `src/draupnir_forge/roles/__init__.py`,
`src/draupnir_forge/roles/base.py`, `tests/test_roles_base.py`
**Design:** `Role` ABC: `name`, `purpose`, `run(ctx: RoleContext) -> RoleResult`
(`RoleResult`: `ok`, `summary`, `artifacts` dict, `events` list,
`escalation` Optional[str]). `RoleContext` dataclass: project_dir,
config, event_log, state, budget, model_router (stub interface for now),
prior artifacts. `RoleRegistry` maps names → classes; `create(name)`.
**Accept:** registry lists 10 roles after waves complete (1 now + growth);
base contract test with a dummy role.

### Slice 10 — Skald (intent interpreter)
**Files:** `src/draupnir_forge/roles/skald.py`, `tests/test_skald.py`,
`data/skald_prompts.yaml`
**Design:** `Skald(Role)`: `run` takes raw user text (+ optional repo path),
produces `SYSTEM_VISION.md` content: goal statement, priorities,
non-goals, success criteria, open ambiguities list. Rule-based v1
(keyword extraction for goals/constraints) + prompt templates in YAML
for future model use. `interpret(text) -> Vision` dataclass.
**Accept:** vision doc generated from sample intent; ambiguities flagged.

### Slice 11 — Cartographer (repository mapper)
**Files:** `src/draupnir_forge/roles/cartographer.py`, `tests/test_cartographer.py`
**Design:** `Cartographer(Role)`: walks repo (ignore `.git`, `__pycache__`,
`node_modules`, binary), builds `DOMAIN_MAP.md` + machine map:
files by language/extension, entry points (`__main__`, `cli.py`, `setup.py`),
test dirs, dependency files (`requirements*.txt`, `pyproject.toml`,
`package.json`), import graph (Python AST-based, stdlib only).
Writes `.mythis/DOMAIN_MAP.md` + `domain_map.json`.
**Accept:** maps this repo itself correctly; import graph finds cycles.

### Slice 12 — Architect (system designer)
**Files:** `src/draupnir_forge/roles/architect.py`, `tests/test_architect.py`,
`data/architect_prompts.yaml`
**Design:** `Architect(Role)`: input vision + domain map → emits
`ARCHITECTURE.md`, `INTERFACES.md`, `INVARIANTS.md` (Markdown) plus
`architecture.json` (domains, ownership, interfaces, dependency direction).
v1: template-driven from domain map clusters (directory = domain heuristic)
+ explicit interface extraction (public functions/classes per module).
**Accept:** generates coherent docs for a sample repo; invariants non-empty.

### Slice 13 — Planner (roadmap compiler)
**Files:** `src/draupnir_forge/roles/planner.py`, `tests/test_planner.py`
**Design:** `Planner(Role)`: architecture + vision → task graph.
`ForgeTask` dataclass per §11 schema (task_id `T-001`, title, domain,
status, depends_on, goal, constraints, acceptance, verification).
Topological sort, `next_ready_task()`, JSON persistence
`.mythis/roadmap.json` + human `ROADMAP.md`. Detects cycles → error.
**Accept:** diamond dependency resolves in order; cycle detected.

### Slice 14 — Forge Worker (implementation agent)
**Files:** `src/draupnir_forge/roles/worker.py`, `tests/test_worker.py`
**Design:** `ForgeWorker(Role)`: executes one bounded task: applies file
patches (unified-diff apply, pure-python `apply_patch()`), runs shell
commands via ToolExecutor interface (slice 24; v1 direct subprocess with
timeout), records diffs to `.mythis/evidence/diffs/`. Guardrails: refuses
paths outside project dir; refuses to edit `.mythis/` canonical docs
directly (Scribe owns those). Returns changed-files list.
**Accept:** applies a sample patch; path-traversal refused.

### Slice 15 — Auditor (adversarial reviewer)
**Files:** `src/draupnir_forge/roles/auditor.py`, `tests/test_auditor.py`,
`data/audit_checks.yaml`
**Design:** `Auditor(Role)`: reviews a diff/file set against YAML checklists:
architecture drift (imports crossing domain boundaries per
`architecture.json`), hardcoded secrets (regex), bare `except:`,
`print(` in library code, TODO/FIXME, complexity (nested depth >4),
duplicated blocks (hash-based). Returns findings list with severity.
**Accept:** finds planted violations in a fixture diff.

### Slice 16 — Tester (runtime reality agent)
**Files:** `src/draupnir_forge/roles/tester.py`, `tests/test_tester.py`
**Design:** `Tester(Role)`: discovers test command (`pytest`/`unittest`/npm),
runs with timeout, parses output (pass/fail counts via regexes),
classifies failures via `failures.py`, writes
`.mythis/evidence/tests/<ts>.log`. Never edits tests to make them pass
(law: tests are witnesses).
**Accept:** runs this repo's own suite; parses counts correctly.

### Slice 17 — Verifier (acceptance judge)
**Files:** `src/draupnir_forge/roles/verifier.py`, `tests/test_verifier.py`
**Design:** `Verifier(Role)`: 8-gate checklist per §17 (code/build/test/
interface/invariant/runtime/goal/documentation). Each gate: `pass|fail|
skip` + evidence string. `evaluate(task, implementation, test_result) ->
Verdict`. Goal gate compares acceptance criteria against observed
evidence (v1: keyword/artifact-presence checks).
**Accept:** verdict dataclass; failing gate blocks completion.

### Slice 18 — Scribe + Heimdallr
**Files:** `src/draupnir_forge/roles/scribe.py`,
`src/draupnir_forge/roles/heimdallr.py`, `tests/test_scribe_heimdallr.py`
**Design:** `Scribe(Role)`: owns `.mythis/` canonical docs; `update()` merges
task outcomes into DECISIONS.md, ROADMAP.md status, KNOWN_ISSUES.md,
CAPABILITY_LEDGER.md (append-only sections, never rewrites history).
`Heimdallr(Role)`: `check()` monitors loop health — repeated failures
(same task 3×), runaway (no progress N cycles), budget near-exhausted;
returns escalations.
**Accept:** scribe appends decision entry; heimdallr flags 3× failure.

---

## WAVE 3 — ORCHESTRATION (slices 19–28)

### Slice 19 — Forge state machine
**Files:** `src/draupnir_forge/machine.py`, `tests/test_machine.py`
**Design:** `ForgeMachine`: states per §8
(INTAKE, DISCOVERY, DEFINITION, ARCHITECTURE, ROADMAP, TASK_READY,
IMPLEMENTING, REVIEWING, TESTING, VERIFYING, COMPLETE_TASK, REPAIR,
REPLAN, HUMAN_DECISION, DOCUMENTING, REGROUNDING, PROJECT_COMPLETE).
Transition table as data (`data/transitions.yaml`). Illegal transition →
`IllegalTransition` error. Persists current state in ProjectState.
**Accept:** full happy-path walk; illegal transition rejected.

### Slice 20 — Forge Orchestrator core loop
**Files:** `src/draupnir_forge/orchestrator.py`, `tests/test_orchestrator.py`
**Design:** `Orchestrator`: `run(project_dir, goal)` drives machine:
dispatches roles per state, handles REPAIR/REPLAN loops with
failure-class escalation (§16 ladder), HUMAN_DECISION → pause + persist.
Deterministic transitions; max cycles guard (Heimdallr). Emits events
per state change.
**Accept:** drives a 2-task mock project to PROJECT_COMPLETE with stub roles.

### Slice 21 — Context Compiler
**Files:** `src/draupnir_forge/context.py`, `tests/test_context.py`
**Design:** `ContextCompiler.build(task, project)` → `ContextPackage`:
vision excerpt, task def, domain files (only affected domain),
interfaces, invariants, relevant decisions (keyword match),
previous failures for this task. Token estimate (chars/4); truncation
policy: drop order defined in data (`data/context_policy.yaml`).
**Accept:** package excludes unrelated domains; token estimate sane.

### Slice 22 — Roadmap compiler (machine task graph)
**Files:** `src/draupnir_forge/roadmap.py`, `tests/test_roadmap.py`
**Design:** (Planner role makes the plan; this is the runtime engine.)
`TaskGraph`: load/save `.mythis/roadmap.json`, `ready_tasks()`,
`mark_complete/mark_failed`, dynamic split (`split_task()` for
TASK_TOO_LARGE), dependency validation. Used by orchestrator.
**Accept:** split preserves dependencies; ready-set correct after completes.

### Slice 23 — Model router
**Files:** `src/draupnir_forge/models.py`, `tests/test_models.py`,
`data/model_prices.yaml` (already in slice 8 — reuse)
**Design:** `ModelRouter`: OpenAI-compatible HTTP client (stdlib
`urllib`, no extra deps), provider config from ForgeConfig,
per-role model prefs (`data/role_models.yaml`), fallback chain,
retry with backoff, `complete(prompt, max_tokens) -> str`.
Records usage to Budget. Graceful offline error (no crash).
**Accept:** mocked HTTP test; fallback on 500; budget charged.

### Slice 24 — Tool executor
**Files:** `src/draupnir_forge/tools.py`, `tests/test_tools.py`
**Design:** `ToolExecutor`: `run(cmd, cwd, timeout, allow_network=False)`
via subprocess; authority levels per §31
(read|write|exec|install|network|destructive) from project config;
denied action → `AuthorityDenied` (never silent). Captures
stdout/stderr, exit code, duration. `apply_patch()` here (used by worker).
**Accept:** timeout kills process; destructive denied by default.

### Slice 25 — Verification engine (8 gates)
**Files:** `src/draupnir_forge/verification.py`, `tests/test_verification.py`
**Design:** `VerificationEngine.run_gates(task, impl, test_result)` executes
§17 gates as functions returning `GateResult(passed, evidence)`.
Interface gate: public API snapshot diff (`.mythis/api_snapshot.json`).
Invariant gate: checks `INVARIANTS.md` machine-checkable lines
(`data/invariants.yaml` mirror). All gates must pass → task DONE.
**Accept:** each gate unit-tested; one failing gate blocks.

### Slice 26 — Project memory (.mythis/ canonical docs)
**Files:** `src/draupnir_forge/memory.py`, `tests/test_memory.py`
**Design:** `ProjectMemory`: ensures `.mythis/` skeleton
(SYSTEM_VISION.md, ARCHITECTURE.md, DOMAIN_MAP.md, INTERFACES.md,
INVARIANTS.md, CONSTRAINTS.md, ROADMAP.md, DECISIONS.md,
KNOWN_ISSUES.md, CAPABILITY_LEDGER.md, PROJECT_STATE.json,
evidence/, sessions/). `read_doc/write_doc` with Scribe-only write
enforcement flag. `snapshot()` → dict for context compiler.
**Accept:** skeleton created; write enforcement works.

### Slice 27 — Human escalation engine
**Files:** `src/draupnir_forge/escalation.py`, `tests/test_escalation.py`
**Design:** `EscalationPolicy`: `needs_human(task, failure_history)` per §13
(8 ask-conditions as predicates). `Escalation` record persisted;
`forge` CLI pauses with a clear question file
`.mythis/awaiting_human.md`; `forge --answer` resumes. Never asks about
obvious next steps (negative list from §13 as data).
**Accept:** predicate tests for all 8 conditions; resume flow works.

### Slice 28 — Pause/resume + git checkpoints
**Files:** `src/draupnir_forge/checkpoints.py`, `tests/test_checkpoints.py`
**Design:** `Checkpointer`: `checkpoint(msg)` → `git add -A` (project dir
only) + commit with forge trailer (`Forge-Task: T-003`); `rollback(n)`;
`pause()` writes `.mythis/PAUSED` with state snapshot; `resume()`
validates repo clean-ish then continues. Uses ToolExecutor (authority:
write).
**Accept:** checkpoint creates commit with trailer (tmp git repo test).

---

## WAVE 4 — MODES & INTERFACES (slices 29–38)

### Slice 29 — New-project mode
**Files:** `src/draupnir_forge/modes_new.py`, `tests/test_modes_new.py`
**Design:** `NewProjectMode.run(goal_text)`: intent → requirements →
constraints → architecture → scaffold (dirs per architecture) →
first thin vertical slice task → verify. Produces working skeleton
project, not speculative sprawl (§23).
**Accept:** creates sample project with passing smoke test.

### Slice 30 — Existing-repo mode
**Files:** `src/draupnir_forge/modes_existing.py`, `tests/test_modes_existing.py`
**Design:** `ExistingRepoMode.run(repo, goal)`: SCAN→MAP→domain model→
detect tests→detect architecture→docs-vs-code diff→risks→confirm goal→
change roadmap→execute (§22). "Map before modifying" enforced:
no worker task until domain map exists.
**Accept:** maps fixture repo; blocks worker pre-map.

### Slice 31 — Black-box progress CLI
**Files:** `src/draupnir_forge/ui.py`, `tests/test_ui.py`
**Design:** `ProgressView.render(state, roadmap)` → §19-style text panel:
overall %, current objective/phase, completed/now/queued lists,
human-decisions-needed, health. `draupnir status` prints it.
Pure function of state (testable without a TTY).
**Accept:** golden-output test on fixture state.

### Slice 32 — Glass-box inspection commands
**Files:** extend `cli.py`; `tests/test_cli_glassbox.py`
**Design:** `draupnir roadmap` (task graph table), `draupnir events`
(tail/filter event log), `draupnir architecture` (print canonical docs),
`draupnir decisions` (ledger). Read-only; never mutates.
**Accept:** each command works against fixture `.mythis/`.

### Slice 33 — Discovery report generator
**Files:** `src/draupnir_forge/discovery.py`, `tests/test_discovery.py`
**Design:** `DiscoveryReport`: combines Cartographer output + test
detection + doc inventory → `DISCOVERY_REPORT.md` with risks section
(no tests? no CI? huge files?).
**Accept:** report contains all §2 discovery bullets.

### Slice 34 — Architecture document generator
**Files:** `src/draupnir_forge/archdocs.py`, `tests/test_archdocs.py`
**Design:** renders `architecture.json` → polished `ARCHITECTURE.md`
(mermaid diagram!), `INTERFACES.md` tables, `INVARIANTS.md` checklist.
Mermaid emitted as text (no rendering dependency).
**Accept:** mermaid block present; tables well-formed.

### Slice 35 — Decision ledger
**Files:** `src/draupnir_forge/decisions.py`, `tests/test_decisions.py`
**Design:** `DecisionLedger`: `record(decision, reason, alternatives,
evidence, consequences, role)` → append to DECISIONS.md with date +
revisit-conditions. `find(topic)` keyword search. Prevents
re-litigation: warns if new decision contradicts prior without
acknowledging it.
**Accept:** contradiction warning triggers.

### Slice 36 — Drift detection
**Files:** `src/draupnir_forge/drift.py`, `tests/test_drift.py`
**Design:** `DriftDetector.compare()`: architecture.json vs live repo
(imports crossing domains, new top-level dirs, interface changes via
api snapshot). Findings → KNOWN_ISSUES.md + triggers REGROUNDING.
**Accept:** planted drift detected in fixture.

### Slice 37 — Self-repair engine
**Files:** `src/draupnir_forge/repair.py`, `tests/test_repair.py`
**Design:** `RepairEngine`: failure → classify → bounded repair task
(small diff) → re-test → if still failing, escalate per ladder (§16).
Preserves evidence; never edits tests to pass (§25). Max 3 repair
attempts per task then REPLAN.
**Accept:** fixes a planted bug; gives up correctly after 3.

### Slice 38 — Re-grounding cycle
**Files:** `src/draupnir_forge/reground.py`, `tests/test_reground.py`
**Design:** `reground(project)`: re-run Cartographer (incremental),
diff domain map, validate roadmap tasks still make sense
(prereqs exist, files exist), Architect revises if drift significant.
Emits PROJECT_REGROUNDED event.
**Accept:** stale roadmap task flagged after file deletion.

---

## WAVE 5 — POLISH, PROOF & RELEASE (slices 39–50)

### Slice 39 — Security model
**Files:** `src/draupnir_forge/security.py`, `tests/test_security.py`,
extend `data/default_config.yaml`
**Design:** authority levels (§31) per project + per role; default-deny
for destructive/network; `authorize(action)` audit-logged.
**Accept:** destructive blocked by default; audit log written.

### Slice 40 — Clean-room research mode
**Files:** `src/draupnir_forge/cleanroom.py`, `tests/test_cleanroom.py`
**Design:** `CleanRoom`: `study(source)` records provenance
(URL, license, date) to `.mythis/provenance.md`; `derive()` produces
requirements/compat/architecture docs; blocks verbatim copying
(canonical 25-char overlap check vs sources).
**Accept:** provenance recorded; overlap check fires.

### Slice 41 — Success metrics dashboard
**Files:** `src/draupnir_forge/metrics.py`, `tests/test_metrics.py`
**Design:** computes §33 metrics from event log: tasks w/o intervention,
interventions/hour, tokens per verified task, regression rate,
autonomous run length. `draupnir metrics` prints table.
**Accept:** metrics correct on synthetic event log.

### Slice 42 — Session memory + replay
**Files:** `src/draupnir_forge/sessions.py`, `tests/test_sessions.py`
**Design:** per-run session dir `.mythis/sessions/<ts>/` with goal.md,
actions.jsonl, findings.md, result.md. `replay(session)` prints
action sequence (for debugging, not re-execution).
**Accept:** session recorded during orchestrator test.

### Slice 43 — Canonical docs: ARCHITECTURE.md etc.
**Files:** `docs/ARCHITECTURE.md`, `docs/DOMAIN_MAP.md`,
`docs/EVENT_MODEL.md`, `docs/SECURITY_MODEL.md`
**Design:** Scribe-grade docs describing THIS repo's actual architecture
(dogfood the archdocs generator where possible).
**Accept:** docs match implementation (reviewed, not generated blindly).

### Slice 44 — Multi-provider model support
**Files:** extend `models.py`, `data/role_models.yaml`, `tests/test_models_multi.py`
**Design:** provider registry: `openai` (default), `ollama`
(`http://localhost:11434`), `custom` (any OpenAI-compatible base URL).
Per-provider config; model switch on repeated failures (§16).
**Accept:** ollama provider builds correct request (mocked).

### Slice 45 — Examples
**Files:** `examples/hello-forge/` (goal.md, expected outputs),
`examples/README.md`
**Design:** worked example: goal text → expected vision/roadmap excerpts.
Used by docs and tests as fixtures.
**Accept:** example fixtures valid.

### Slice 46 — Dogfood thin vertical slice
**Files:** (none new — uses the forge itself)
**Design:** run `draupnir forge` on THIS repo with a tiny real feature
(e.g., "add `draupnir events --since` filter") through the full loop:
intent→…→verify→commit. Proves the concept end-to-end (§37 milestone).
**Accept:** feature lands via the forge loop with green tests.

### Slice 47 — End-to-end integration test
**Files:** `tests/test_e2e.py`
**Design:** spins a fixture mini-repo, runs Orchestrator with stub model
(scripted responses) through new-project mode to PROJECT_COMPLETE;
asserts checkpoints, events, docs, tests all exist.
**Accept:** green, < 120s.

### Slice 48 — Packaging & install docs
**Files:** `pyproject.toml` (finalize), `docs/INSTALL.md`
**Design:** `pip install -e .` clean on fresh checkout; `draupnir`
console script; version pinned deps (pyyaml only); INSTALL.md
with quickstart.
**Accept:** fresh-venv install test (scripted).

### Slice 49 — FINAL_REPORT generator
**Files:** `src/draupnir_forge/report.py`, `tests/test_report.py`
**Design:** `FinalReport`: at PROJECT_COMPLETE emits FINAL_REPORT.md
per §26 (what built, architecture, how to run, verification,
limitations, future ideas, risks).
**Accept:** golden test on fixture project.

### Slice 50 — Release: README quickstart + final push
**Files:** `README.md` (quickstart section), `docs/ROADMAP.md`
**Design:** README gains 20-line quickstart; ROADMAP.md marks all 50
done; full suite green; final push verified with `git ls-remote`.
**Accept:** suite green; remote HEAD matches local.

---

## Execution notes for coordinators

- Batches: Wave 1 slices 2–8 depend only on slice 1's layout → parallelize.
  Roles (10–18) depend on slice 9 → parallelize after 9 lands.
  Orchestration (19–28) needs roles + foundation → mostly sequential,
  but 21/23/24/26/28 can parallelize once interfaces are fixed.
- Each implementation worker: clone repo to private dir, implement slice
  (code + tests), run `python -m unittest`, report file list + results.
  Coordinator integrates, runs full suite, commits, pushes.
- Push discipline: fetch, diff, preserve Volmarr's edits, never force-push,
  verify `git ls-remote origin HEAD`.
