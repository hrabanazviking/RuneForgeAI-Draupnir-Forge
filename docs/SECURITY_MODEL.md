# Draupnir Forge — Security Model

*Slice 43 canonical doc, refreshed through slice 49. The security posture
as actually implemented (slices 1–49): authority levels, the
`SecurityPolicy` in `src/draupnir_forge/security.py` (slice 39),
default-deny policy, and the audit trail.*

## 1. Authority levels

`tools.py` defines the six authority kinds (spec §31). Every
`ToolExecutor.run()` call names one; the kind gates the action:

| Kind | Config flag | Default | Meaning |
|---|---|---|---|
| `read` | — (always allowed) | allowed | Reading files, listing directories |
| `write` | — (always allowed) | allowed | Writing files, applying patches |
| `exec` | `tools.allow_exec` | **true** | Running shell commands |
| `install` | `tools.allow_install` | **false** | Installing packages / toolchains |
| `network` | `tools.allow_network` | **false** | Network access during commands |
| `destructive` | `tools.allow_destructive` | **false** | Deleting, force-push, `rm -rf`, etc. |

The mapping lives in `tools._KIND_TO_FLAG`; the set of kinds is
closed — an unknown kind raises `ValueError`, it never silently
passes.

## 2. Default-deny policy

- `network`, `install`, and `destructive` are **denied by default**
  (`data/default_config.yaml`). A denied action raises
  `AuthorityDenied` *before anything spawns* — denial is loud, never
  silent, and never a crash: the orchestrator treats it as a failure
  to classify and escalate.
- The authority check happens at the single choke point
  (`ToolExecutor._check_authority`), so no role can bypass it by
  calling subprocess directly — roles only get a `ToolExecutor`.
- Overrides are layered like all config
  (`~/.draupnir/config.yaml` < `.mythis/config.yaml` < `DRAUPNIR_*`
  env < explicit), so a project can tighten — or a human can
  deliberately loosen — authority with full visibility.

## 3. Containment beyond the authority check

- **Path confinement:** `apply_patch()` refuses patches that escape
  `project_dir` (`..` traversals, absolute paths outside the root).
- **Scribe's monopoly:** patches touching `.mythis/` are refused —
  only the Scribe role writes the canonical docs, so a worker cannot
  forge history, decisions, or state.
- **Timeouts kill:** `ToolExecutor.run()` enforces `timeout_s`
  (default 300) and tears down the whole process tree on expiry.
- **Output caps:** stdout/stderr captured at 100 KiB each — a runaway
  command cannot flood memory.
- **Autonomy levels:** `guided` asks before architecture changes,
  dependencies, destructive actions, and deployment; `trusted` works
  autonomously inside the agreed architecture; `deep` runs until
  complete, budget, or a hard blocker. The human, not the model,
  chooses the level.

## 4. Secrets handling

- API keys are **never in config files**: `model.api_key_env` names
  the environment variable; the router reads the value at call time.
- The Auditor's checklist (`data/audit_checks.yaml`,
  `hardcoded_secrets`) greps diffs for literal key/password/token
  assignments and flags them.
- Project law (RULES.AI.md): never hardcode secrets, settings, or
  data in code — data files or environment only.

## 5. Economic safety

`budget.py` caps every run: `budget.max_tokens` (default 2,000,000)
and `budget.max_cost_usd` (default 25.00). Every model call is
charged; breaching either cap raises `BudgetExhausted`, which the
orchestrator handles instead of crashing. A runaway model cannot
spend without bound.

## 6. Audit log

Three independent trails, all under the project's `.mythis/`:

1. **Event ledger** — `events.jsonl`: every important action as a
   structured event (`seq`, UTC `ts`, `type`, `actor_role`, `payload`,
   `caused_by`). Append-only; each append is flushed and fsync'ed.
2. **Forge log** — `logs/forge.log`: rotating file log with role
   binding (`bind_role`), carrying warnings like provider failovers
   and denied authorities.
3. **Git checkpoints** — `Checkpointer` commits with `Forge-Task:
   T-003` trailers; `rollback(n)` restores, `pause`/`resume`
   snapshot the run.

Together: *what* happened (events), *why it was said* (log), and
*what the tree looked like* (git).

## 7. Slice 39: `security.py` (implemented)

`src/draupnir_forge/security.py` turned the config-flag checks into a
first-class, auditable policy object:

- **`Authority`** (`IntEnum`) — READ < WRITE < EXEC < INSTALL < NETWORK
  < DESTRUCTIVE, ordered by power, so comparisons hold by construction.
- **`SecurityPolicy`** — `authorize(action)` / `check_or_raise(action)`
  judge every tool action against two gates: the config gate
  (`tools.allow_*` flags; INSTALL/NETWORK/DESTRUCTIVE denied by
  default) and the role gate (`data/role_authority.yaml` grants per
  role beyond the universal READ+WRITE baseline). An action passes
  only when *both* permit it — a role grant never overrides a config
  denial.
- **Audit** — every decision is audit-logged (unless `tools.audit` is
  false) to `<project>/.mythis/security_audit.jsonl`, recording who
  asked, what was asked, the verdict, and why. Audit write failures
  log warnings, never raise — the audit trail never sinks the forge.

This document, with §7, is the complete and accurate statement of
the Forge's security model.
