# Slice 46 — Dogfood findings: the forge ran itself

*Date: 2026-10-09. Method: Mythic Engineering 6-phase, executed by the
coordinator directly (the loop needed the fully integrated system).*

## Skald — vision

Prove the Draupnir Forge end-to-end (§37 milestone): run `draupnir
forge`'s real `Orchestrator` with the real roles on a scratch copy of
this repo, implementing a tiny real feature — `draupnir events
--since-time <ISO-8601>` — through the full loop:
intent → discovery → definition → architecture → roadmap → implement →
review → test → verify → document → commit.

## Rúnhild — design

No model API key exists in this environment, so the patch content
itself stands in for model output: a hand-written unified diff seeded
into the orchestrator's artifacts (`task_specs`, `patch`, `commands`),
exactly where a model-written patch would land. **Everything
downstream of the seed runs for real**: the Worker applies the patch,
the Auditor reviews the diff, the Tester runs the suite, the Verifier
judges the 8 gates, the Scribe documents, the machine transitions,
events append, checkpoints commit.

Driver: `~/workspace/dogfood_driver.py`. Scratch repo:
`~/workspace/dogfood-run/` (fresh `git init`, baseline commit).

## Eldra — implementation

The feature (`_parse_iso_ts`, `--since-time` flag, filter logic,
4 tests) was written, unit-tested, then reverted into
`~/workspace/dogfood.patch` for the loop to apply.

## Sólrún — test/verify: the loop found four real bugs

The dogfood failed three times before completing — each failure a
genuine integration bug, fixed additively:

1. **The run's goal never reached the Skald.** `_planning_step`
   didn't forward the goal; the Skald starved. Fix: publish
   `artifacts["goal_text"]` in `_planning_step`
   (`orchestrator.py`).
2. **Cartographer/Architect artifact mismatch.** The Cartographer
   emits a `DomainMap` object; the Architect demanded a mapping.
   Fix: the Architect accepts the chart via its `.domains`
   (`roles/architect.py`). The Cartographer's pinned test is
   untouched.
3. **No `implementation` record for the Verifier.** Nothing built
   the record the 8-gate judge consumes, so the goal gate failed
   hard. Fix: the orchestrator synthesizes it from the Worker's
   `changed_files` + the Tester's `test_result`, attesting the
   acceptance criteria (`orchestrator.py`).
4. **Stale-state dispatch: REPLAN → REPAIR illegal transition.**
   The run loop read `machine.current()` *before* the Heimdallr
   watch; when the watch moved VERIFYING → REPLAN, the stale
   VERIFYING handler ran `_route_failure` → `go("REPAIR")` against
   the REPLAN state. Fix: re-read the state after the watch
   (`orchestrator.py`).
5. **Tester didn't put the project's `src/` on `PYTHONPATH`.**
   New-project smoke tests passed only when a *relative*
   `PYTHONPATH=src` accidentally resolved into the scaffold;
   absolute paths broke them. Fix: `run_tests` prepends
   `<project>/src` (`roles/tester.py`); the modes_new
   re-verification test does the same (`tests/test_modes_new.py`).

## Védis — integration

Completion rites wired into the loop (all best-effort, never raise):

- `_take_checkpoint` after every task (`Forge-Task:` trailer) and at
  PROJECT_COMPLETE — the "commit" in intent→…→verify→commit.
- `_write_final_report` at PROJECT_COMPLETE (slice 49's generator,
  previously unwired).

## Scribe — the successful run

```
{"status": "complete", "tasks_done": 1, "cycles": 14,
 "state": "PROJECT_COMPLETE"}
```

Verified in the scratch repo: the `--since-time` feature live and
working, the loop's own Tester green, `events.jsonl` with the full
trail (14 events incl. TASK_COMPLETED, TEST_PASSED,
PROJECT_COMPLETED), `.mythis/FINAL_REPORT.md` written, and a git
checkpoint `forge: task T-001 complete` carrying the feature.

## Honest caveats

- The patch *content* was hand-written (the model stand-in). The
  loop mechanics are proven; the intelligence that writes patches
  in production is a model call that doesn't exist in this
  environment.
- Patch re-application is not idempotent: a repair retry of the
  same static patch fails on context mismatch. In production the
  repair step would carry a fresh model-written patch; making the
  Worker detect already-applied hunks is a future slice.
- `docs/ROADMAP.md` records these findings for the release.
