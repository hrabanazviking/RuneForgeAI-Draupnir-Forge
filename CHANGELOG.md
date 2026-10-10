# CHANGELOG — RuneForgeAI-Draupnir-Forge

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
