# CHANGELOG — RuneForgeAI-Draupnir-Forge

## 2026-10-11 — Dawn forge: 20 slices ("The Weaving of the Wyrd-Threads")

Inner-communication run: honest handoffs between subsystems — public graph
introspection, bounded discovery, serialized failure/audit memory, sanitized
operator surfaces, validated inputs at every seam (HEAD c67ae30).
Suite: **876 passed, 0 failed** (`PYTHONPATH=src python3 -m pytest tests -q`).
Pushed and verified: local HEAD == `git ls-remote origin HEAD`.

Slices:
1. roadmap/find-cycles — public `TaskGraph.find_cycles()` enumerates all dependency cycles
2. roadmap/critical-path — `critical_path()` returns the longest dependency chain (cycle-safe)
3. roadmap/add-task-title-required — `add_task()` raises ValueError on blank titles
4. discovery/file-size-cap — files over `max_file_bytes` (5 MiB) skipped and listed
5. discovery/write-atomic — DISCOVERY_REPORT.md written via tmp + fsync + replace
6. failures/record-serialize — `FailureRecord.to_dict()`/`from_dict()` round-trip (unknown class → UNKNOWN)
7. ui/control-char-sanitize — ANSI/C0/C1 stripped from titles, statuses, goal, health in both renderers
8. modes_new/slug-max-length — `slugify_package_name()` capped at 64 chars, always a valid identifier
9. modes_existing/empty-goal — `build_roadmap("")`/`run("")` raise ValueError naming "goal"
10. modes_existing/detect-tests-nonnull — `detect_tests()` returns `List[str]`, never None
11. decisions/record-validation — `record()` raises ValueError on empty decision text
12. decisions/dedupe — identical consecutive decisions are not appended twice
13. reground/dry-run — `reground(..., dry_run=True)` returns the plan, writes nothing
14. cli/roadmap-validate — `draupnir roadmap --validate` → exit 0/2 with flaw message
15. verifier/gateresult-serialize — `GateResult.to_dict()`/`from_dict()` round-trip
16. auditor/finding-serialize — `Finding.from_dict()` round-trips `to_dict()` (never raises)
17. tester/evidence-atomic — test evidence logs written atomically
18. report/task-stats — FinalReport carries an "N of M tasks complete" line
19. archdocs/missing-keys — `render_architecture()`/`render_interfaces()` raise ValueError on missing keys
20. scribe/roadmap-status-validation — unknown status raises ValueError instead of silent uncheck

Note: slice 19 intentionally replaced two old test_archdocs.py assertions
(empty-arch silent stub → now ValueError); tests updated to the new contract.

## 2026-10-10 — Dusk forge: 20 slices ("The Hardening of the Hearth")

Second-wave hardening run on top of the completed 50-slice roadmap (HEAD 482401c).
Suite: **799 passed, 0 failed** (`PYTHONPATH=src python3 -m pytest tests -q`).
Pushed and verified: local HEAD == `git ls-remote origin HEAD`.

Slices:
1. events/payload-cap — `emit()` rejects oversized payloads (default 256 KiB) before touching the ledger
2. events/verify-chain — `verify_integrity()` reports seq gaps and corrupt lines
3. escalation/pending-age — `pending_age_seconds()` / `is_stale()` from the Asked timestamp
4. escalation/discard-stale — `discard_stale_escalation()` removes dead waits, stamps the ledger
5. orchestrator/stale-escalation-guard — run start discards stale escalations instead of hanging
6. models/429-retryable — HTTP 429 retries honoring Retry-After (capped)
7. models/circuit-breaker — per-provider consecutive-failure skip with cooldown
8. log/secret-redaction — `redact_secrets()` masks API-key-like tokens in log output
9. tools/output-cap — stdout/stderr truncated at `tools.max_output_bytes` (default 1 MiB) with marker
10. tools/apply-patch-dry-run — `apply_patch(..., dry_run=True)` validates without touching files
11. repair/pre-snapshot — best-effort git checkpoint before a repair patch; `snapshot_ref` in history
12. config/skipped-files — `ForgeConfig.skipped_files` names layer + reason; surfaced by `summary()`
13. sessions/atomic-writes — session files written via tmp + fsync + replace
14. budget/near-exhaustion — `is_near_exhausted()` / one-shot `check_warning()` / WARNING in report
15. verification/gate-timeout — interface/invariant checks time out (default 60s) into violation notes
16. machine/transition-audit — last 32 transitions kept; `recent_transitions()` oldest-first
17. drift/baseline-age — baseline timestamps; staleness noted in the drift report
18. memory/atomic-write-doc — `write_doc()` atomic; crash can't leave half-written docs
19. orchestrator/run-summary — run results carry a one-line human summary
20. cli/failure-exit-code — unexpected handler exceptions → exit 1 (0 ok / 2 usage preserved)

Commits: 8107124, 594464b, 42a71cb, 60ae3aa, d8c742c, 7f0a26d (+ CHANGELOG).
