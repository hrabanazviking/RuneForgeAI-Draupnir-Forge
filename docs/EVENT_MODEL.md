# Draupnir Forge — Event Model

*Slice 43 canonical doc. The 18 event types of `EventType`
(`src/draupnir_forge/events.py`), when each fires, who emits it, and
what it carries. Verified against the emission sites in the source.*

## The ledger

Events live in `<project>/.mythis/events.jsonl`, one JSON object per
line, via `EventLog`. Each `ForgeEvent` carries:

| Field | Meaning |
|---|---|
| `seq` | Monotonic sequence number (survives restarts; scan-on-open) |
| `ts` | UTC ISO-8601 timestamp |
| `type` | One of the 18 `EventType` values (closed set) |
| `actor_role` | Which role emitted it (`"orchestrator"`, `"tester"`, `"heimdallr"`, …) |
| `project` | Project name (from the project directory name) |
| `payload` | JSON-serializable detail dict |
| `caused_by` | `seq` of the causal parent event, if any |

Appends are crash-safe (flush + fsync per `emit()`); corrupt lines are
skipped and counted, never fatal. `EventLog.query(type, since_seq,
limit)` reads back in chronological order. Read the log any time with
`draupnir events [--type T] [--since SEQ] [--limit N]`.

## The 18 event types

### Project lifecycle

| Type | Fires when | Emitted by |
|---|---|---|
| `PROJECT_CREATED` | A forge run starts (`Orchestrator.run`, INTAKE) | `orchestrator.py` (`_handle_intake`) |
| `PROJECT_COMPLETED` | The machine reaches `PROJECT_COMPLETE` | `machine.py` (state entry) and `orchestrator.py` (run end) |
| `PROJECT_REGROUNDED` | A re-grounding cycle finishes | `reground.py`; also `machine.py` on entering `REGROUNDING` |

### Vision, domain, architecture, roadmap

| Type | Fires when | Emitted by |
|---|---|---|
| `VISION_UPDATED` | Skald's vision is written/updated | `machine.py` on entering `DEFINITION` |
| `DOMAIN_DISCOVERED` | Cartographer finishes the repo map | `machine.py` on entering `DISCOVERY` |
| `ARCHITECTURE_UPDATED` | Architect writes architecture docs | `roles/architect.py` (payload: domain names, interface/invariant counts); also `machine.py` on entering `ARCHITECTURE` |
| `ROADMAP_REVISED` | Planner compiles/revises the task graph, or the machine enters `REPLAN` | `roles/planner.py` (payload: task count + ids); `machine.py` on entering `ROADMAP` or `REPLAN` |

### Task lifecycle

| Type | Fires when | Emitted by |
|---|---|---|
| `TASK_STARTED` | A task enters `IMPLEMENTING` | `machine.py` |
| `TASK_COMPLETED` | A task passes verification (`COMPLETE_TASK`) | `machine.py` |
| `TASK_FAILED` | A task fails (enters `REPAIR`) | `machine.py`; `orchestrator.py` (with stage + failure class) |
| `TASK_REPAIRED` | *(Reserved.)* A bounded repair succeeds and the task returns to the loop | — not yet emitted; the repair engine (slice 37) is the intended emitter |

### Tests

| Type | Fires when | Emitted by |
|---|---|---|
| `TEST_PASSED` | A test run completes with 0 failures/errors | `roles/tester.py` (payload: passed/failed/errors/skipped, duration, command); `orchestrator.py` |
| `TEST_FAILED` | A test run has failures/errors, or no test command was discovered | `roles/tester.py` (payload: counts + reason) |

### Invariants and humans

| Type | Fires when | Emitted by |
|---|---|---|
| `INVARIANT_VIOLATED` | Heimdallr's watch finds a violated invariant mid-run | `orchestrator.py` (`_heimdallr_watch`, actor `"heimdallr"`) |
| `HUMAN_DECISION_REQUESTED` | The Forge pauses for human judgment | `machine.py` on entering `HUMAN_DECISION`; `orchestrator.py` (with the question) |
| `HUMAN_DECISION_RECEIVED` | *(Reserved.)* The human answers and the run resumes | — not yet emitted; the escalation resume path (slice 27) is the intended emitter |

### Operations

| Type | Fires when | Emitted by |
|---|---|---|
| `MODEL_SWITCHED` | *(Reserved.)* The `ModelRouter` fails over to the next provider in `model.fallback_providers` | — not yet emitted; slice 44 logs failover at WARNING (`"Model router failing over to provider …"`); event emission lands when the orchestrator owns the router lifecycle (slice 46+) |
| `CHECKPOINT_CREATED` | *(Reserved.)* A git checkpoint is taken | — not yet emitted; `checkpoints.py` is the intended emitter |

## Emission patterns

- **State-entry events** (`machine.py::_announce`): `DISCOVERY`,
  `DEFINITION`, `ARCHITECTURE`, `ROADMAP`, `IMPLEMENTING`,
  `COMPLETE_TASK`, `REPAIR`, `REPLAN`, `HUMAN_DECISION`,
  `REGROUNDING`, `PROJECT_COMPLETE` each map to one event type via
  `STATE_EVENT_MAP`. Payload: `{"from", "to", "goal", "task_id"?}`.
  Emission is best-effort — a ledger failure never breaks the walk.
- **Role events** (`Role.emit` in `roles/base.py`): roles announce
  their own outcomes (tester, planner, architect today).
- **Orchestrator events**: lifecycle and watch events
  (`PROJECT_CREATED/COMPLETED`, `TASK_FAILED`, `TEST_PASSED`,
  `INVARIANT_VIOLATED`, `HUMAN_DECISION_REQUESTED`).

## Reading the log

```bash
draupnir events --limit 20          # last 20 events
draupnir events --type TASK_FAILED # only failures
draupnir events --since 142        # everything after seq 142
```

The metrics consumer (slice 41) and session replay (slice 42) both read
this same ledger — it is the Forge's single source of truth about its
own deeds.
