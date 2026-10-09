# Slices 39–40 — Findings (Eldra phase, 2026-10-09)

## What exists today

- **`src/draupnir_forge/tools.py`** (slice 24): `ToolExecutor.run(cmd, ...)`
  pins cwd to project root, passes argv lists only, caps output at
  100 KiB. `_KIND_TO_FLAG` maps kinds → config flags:
  `read→None, write→None, exec→tools.allow_exec, install→tools.allow_install,`
  `network→tools.allow_network, destructive→tools.allow_destructive`.
  Denied kinds raise `AuthorityDenied` before anything spawns. Config
  read failures degrade to deny. This is the canonical per-kind gate that
  slice 39's `SecurityPolicy` must mirror (per-action authority, not replace).
- **`src/draupnir_forge/config.py`** (slice 2): `ForgeConfig.load()` merges
  5 layers (defaults < user < project < env < overrides), validates against
  `data/config_schema.yaml` (every schema key REQUIRED in merged config).
  `get(dotted_key, default)` reads dotted paths like `tools.allow_network`.
- **`data/default_config.yaml`**: `tools:` block already has
  `allow_exec: true, allow_install: false, allow_network: false,`
  `allow_destructive: false, default_timeout_s: 300`. Roadmap slice 39 says
  "extend data/default_config.yaml" — one new key needed: `tools.audit`
  (default true) so every `authorize()` decision is audit-logged by default.
- **`src/draupnir_forge/_paths.py`**: `find_data_file(name)` /
  `load_data_yaml(name)` locate `data/` files without cwd dependence —
  the loader `SecurityPolicy` will use for `role_authority.yaml`.
- **Roles** (`src/draupnir_forge/roles/`): architect, auditor, cartographer,
  heimdallr, planner, scribe, skald, tester, verifier, worker — the role
  names the new `data/role_authority.yaml` must cover.
- **Tests** run via `PYTHONPATH=src python3 -m unittest` (unittest stdlib).

## What slice 39 needs (new)

- `src/draupnir_forge/security.py`:
  - `Authority` IntEnum: READ(1) < WRITE(2) < EXEC(3) < INSTALL(4) <
    NETWORK(5) < DESTRUCTIVE(6) — ordered by power; mirrors tools.py kinds.
  - `SecurityPolicy(config, project_dir=None)`:
    `authorize(action, context) -> bool` — grants only when (a) the
    config flag for that authority allows it (NETWORK/DESTRUCTIVE/INSTALL
    default-deny; READ/WRITE always), AND (b) when `context["role"]` is
    set, the role's grant list in `data/role_authority.yaml` includes it
    (baseline READ+WRITE for every role; unknown role → baseline).
    Each decision is audit-logged unless `tools.audit` is false.
  - `audit(action, granted, actor)` → appends JSON line to
    `<project>/.mythis/security_audit.jsonl` (ts, action, actor,
    granted, reason); never crashes (warn + continue).
  - `check_or_raise(action, actor, context=None)` → raises
    `AuthorityDenied` (imported from `tools.py`) on denial.
- `data/role_authority.yaml` (new data file): role → extra authorities.
  tester:[EXEC], worker:[WRITE, EXEC], auditor/planner/scribe/skald:[]
  (baseline), architect/cartographer/heimdallr/verifier:[EXEC];
  NO role gets DESTRUCTIVE by default.
- `tests/test_security.py`: enum ordering, default-deny NETWORK/
  DESTRUCTIVE/INSTALL, role grants and denials, audit file written,
  `check_or_raise` raising.

## What slice 40 needs (new)

- `src/draupnir_forge/cleanroom.py`: `CleanRoom(project_dir)` with
  - `study(source_id, url, license, notes)` → appends a provenance entry
    (source, url, license, date, notes) to `.mythis/provenance.md` and a
    machine registry sidecar `.mythis/cleanroom_sources.json`
    (so notes survive process restarts; full source TEXT is never stored).
  - `derive(requirements) -> dict` → `{"requirements": [...],
    "compat_notes": [...], "architecture_sketch": [...]}` template docs,
    every line stamped "derived, not copied", citing which studied sources
    informed it; license-aware compat notes (permissive vs copyleft).
  - `check_overlap(candidate_text) -> list[str]` → 25-char sliding windows
    (case-insensitive), O(n) via a prebuilt window set from notes;
    returns the candidate's own matching 25-char snippets (verbatim alarm).
  - `report() -> str` → provenance summary markdown (policy statement,
    source table, license tally).
- `tests/test_cleanroom.py`: provenance recorded, derive shape + stamp,
  overlap fires on a 25-char verbatim snippet from notes, stays silent on
  original text, full text never persisted.

## Decisions / deviations to flag

- RULES.AI.md asks for findings/proposal MDs + human approval before code
  changes; the parent task explicitly ordered immediate implementation of
  slices 39–40, so implementation proceeds in this session (approval step
  skipped by parent instruction).
- `SecurityPolicy(config)` signature kept; `project_dir` added as an
  optional second parameter (None → cwd) — additive only.
- New required config key `tools.audit` added to BOTH default_config.yaml
  and config_schema.yaml (schema requires every key present; merged config
  always carries it).
- No commit/push per task instructions (RULES.AI.md says push often —
  overridden by parent: DO NOT commit/push).
