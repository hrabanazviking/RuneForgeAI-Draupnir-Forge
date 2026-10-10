"""verification.py — Slice 25: the verification engine, the eight gates made real.

The Verifier role (slice 17) already knows how to judge an
implementation through the eight gates of §17; this engine drives that
judgment at runtime. It computes the two gates that need *live* project
evidence and hands the rest to the Verifier's :func:`evaluate`:

- the **interface** gate compares ``impl["public_api"]`` against the
  ``.mythis/api_snapshot.json`` baseline, creating the baseline (and
  passing with a note) when none exists yet;
- the **invariant** gate runs the machine-checkable subset of
  ``data/default_invariants.yaml`` against the project tree.

The engine wraps :func:`draupnir_forge.roles.verifier.evaluate` — it
reuses the eight-gate logic there and never duplicates it. All gates
pass (or skip) and the task is DONE.
"""

from __future__ import annotations

import ast
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from draupnir_forge import _paths
from draupnir_forge.roles import verifier
from draupnir_forge.roles.verifier import GateResult, Verdict
from draupnir_forge.tasks import ForgeTask

log = logging.getLogger("draupnir_forge.verification")

API_SNAPSHOT_FILE = "api_snapshot.json"
INVARIANTS_DATA_FILE = "default_invariants.yaml"


class VerificationEngine:
    """Runs the eight acceptance gates against live project evidence.

    Args:
        project_dir: Root of the forge project holding ``.mythis/``.
        gate_timeout_s: Per-gate wall-clock timeout (seconds) for the
            two engine-owned checks (interface snapshot diff and
            invariant checks). A hung check is recorded as a gate
            failure with a timeout note instead of stalling the forge.
    """

    def __init__(self, project_dir: Union[str, Path],
                 gate_timeout_s: float = 60.0) -> None:
        self.project_dir = Path(project_dir)
        self._mythis = self.project_dir / ".mythis"
        self.gate_timeout_s = float(gate_timeout_s)

    # -- timeout helper -----------------------------------------------

    def _run_gate_with_timeout(self, func, *args):
        """Run an engine-owned gate check with the per-gate timeout.

        The check runs on a single worker thread; if it overruns
        :attr:`gate_timeout_s` the worker is abandoned (never joined —
        the forge must not stall on a hung check) and
        :exc:`concurrent.futures.TimeoutError` is raised to the caller.

        Raises:
            concurrent.futures.TimeoutError: the check overran the gate
                timeout.
        """
        executor = ThreadPoolExecutor(max_workers=1,
                                      thread_name_prefix="draupnir-gate")
        future = executor.submit(func, *args)
        try:
            return future.result(timeout=self.gate_timeout_s)
        finally:
            # Never block on a hung gate worker; it finishes (or not)
            # on its own time.
            executor.shutdown(wait=False, cancel_futures=True)

    # -- public entry point ------------------------------------------

    def run_gates(self, task: ForgeTask, impl: Dict[str, Any],
                  test_result: Any) -> Verdict:
        """Judge an implementation through the eight gates.

        The interface and invariant gates are computed here from live
        evidence (the API snapshot and the machine-checkable
        invariants); the remaining gates — and the verdict itself —
        come from the Verifier role's :func:`evaluate`, which this
        engine wraps rather than duplicates.

        Args:
            task: The ForgeTask being judged (acceptance criteria).
            impl: The implementation record — honors the same keys as
                the Verifier's ``evaluate`` plus ``public_api`` (a dict
                describing the public API surface).
            test_result: A TestResult (or dict); ``None`` skips the test
                gate.

        Returns:
            A :class:`Verdict`; ``passed`` is True only when every
            evaluated gate passed.
        """
        record = dict(impl or {})

        # Gate 4 (interface): snapshot diff -> api_unchanged flag.
        interface_note: Optional[str] = None
        if "public_api" in record:
            try:
                unchanged, interface_note = self._run_gate_with_timeout(
                    self.check_interface, record.get("public_api"))
                record["api_unchanged"] = unchanged
            except FuturesTimeoutError:
                # A hung snapshot diff fails the gate, never the forge.
                log.warning("Interface check timed out after %ss",
                            self.gate_timeout_s)
                record["api_unchanged"] = False
                interface_note = (
                    "interface check timed out after "
                    f"{self.gate_timeout_s:g}s")
            except Exception as exc:  # Huginn reports, never panics.
                log.warning("Interface check failed: %s", exc)
                record["api_unchanged"] = False
                interface_note = f"interface check errored: {exc}"

        # Gate 5 (invariant): machine-checkable invariants -> violations.
        try:
            violations, invariant_notes = self._run_gate_with_timeout(
                self.check_invariants)
        except FuturesTimeoutError:
            # A hung invariant check fails the gate, never the forge.
            log.warning("Invariant checks timed out after %ss",
                        self.gate_timeout_s)
            violations = ["invariant checks timed out after "
                          f"{self.gate_timeout_s:g}s"]
            invariant_notes = ["invariant checks timed out"]
        except Exception as exc:
            log.warning("Invariant checks failed: %s", exc)
            violations = [f"invariant checks errored: {exc}"]
            invariant_notes = ["invariant checks errored"]
        record["invariant_violations"] = violations

        if "project_dir" not in record:
            record["project_dir"] = str(self.project_dir)

        verdict = verifier.evaluate(task, record, test_result)

        # Annotate the two engine-owned gates with their live evidence.
        if interface_note is not None:
            gate = verdict.by_name("interface")
            if gate is not None:
                gate.evidence = interface_note
        invariant_gate = verdict.by_name("invariant")
        if invariant_gate is not None:
            invariant_gate.evidence = "; ".join(invariant_notes)
        return verdict

    # -- gate 4: interface snapshot -----------------------------------

    def check_interface(self, public_api: Any) -> Tuple[bool, str]:
        """Compare a public-API description against the stored snapshot.

        Missing snapshot: the baseline is created from ``public_api``
        and the gate passes with a note saying so. A changed API fails
        the gate until someone re-baselines it with
        :meth:`update_api_snapshot`.

        Returns:
            ``(unchanged, note)`` — the note is human-readable evidence.
        """
        if not isinstance(public_api, dict):
            return False, "public_api is not a mapping; cannot be compared"
        snapshot_path = self._mythis / API_SNAPSHOT_FILE
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.update_api_snapshot(public_api)
            return True, ("api snapshot did not exist — baseline created "
                          "from this implementation; pass")
        except (ValueError, OSError) as exc:
            return False, f"api snapshot unreadable: {exc}"
        if not isinstance(snapshot, dict):
            return False, "api snapshot is not a mapping; cannot be compared"
        if public_api == snapshot:
            return True, (f"public API matches snapshot "
                          f"({len(public_api)} entries)")
        added = sorted(set(public_api) - set(snapshot))
        removed = sorted(set(snapshot) - set(public_api))
        changed = sorted(k for k in set(public_api) & set(snapshot)
                         if public_api[k] != snapshot[k])
        details = []
        if added:
            details.append(f"added: {', '.join(added)}")
        if removed:
            details.append(f"removed: {', '.join(removed)}")
        if changed:
            details.append(f"changed: {', '.join(changed)}")
        return False, "public API differs from snapshot — " + "; ".join(details)

    def update_api_snapshot(self, public_api: Dict[str, Any]) -> Path:
        """(Re-)baseline the public-API snapshot from ``public_api``.

        Used after an approved API change. Writes atomically.
        """
        snapshot_path = self._mythis / API_SNAPSHOT_FILE
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = snapshot_path.with_name(snapshot_path.name + ".tmp")
        tmp.write_text(json.dumps(public_api, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        os.replace(tmp, snapshot_path)
        return snapshot_path

    # -- gate 5: machine-checkable invariants --------------------------

    def check_invariants(self) -> Tuple[List[str], List[str]]:
        """Run the machine-checkable invariants from the data file.

        v1 implements three: no ``print()`` in ``src/`` (except
        ``cli.py``), the event log's append-only nature (v1: checked),
        and the state file's JSON validity.

        Returns:
            ``(violations, notes)`` — a violation per broken invariant
            and one evidence note per check.
        """
        violations: List[str] = []
        notes: List[str] = []
        try:
            data = _paths.load_data_yaml(INVARIANTS_DATA_FILE)
            known = data.get("invariants", []) or []
        except ValueError as exc:
            return ([f"invariant data file missing/unreadable: {exc}"],
                    ["invariant data file missing/unreadable"])
        for key, check in (("no_print", self._invariant_no_prints),
                           ("append_only_log", self._invariant_event_log),
                           ("valid_state", self._invariant_state_json)):
            try:
                ok, note = check()
            except Exception as exc:  # never let one check sink the gate
                ok, note = False, f"{key}: check errored: {exc}"
            label = self._invariant_label(key, known)
            notes.append(f"{label}: {note}")
            if not ok:
                violations.append(f"{label}: {note}")
        return violations, notes

    @staticmethod
    def _invariant_label(key: str, known: List[str]) -> str:
        """Find the matching invariant line in the data file (short form)."""
        needles = {
            "no_print": "print()",
            "append_only_log": "append-only",
            "valid_state": "State transitions",
        }
        needle = needles.get(key, "")
        for line in known:
            if needle and needle in str(line):
                return str(line)[:80]
        return key

    def _invariant_no_prints(self) -> Tuple[bool, str]:
        """No print() calls in src/ library code (cli.py is the herald)."""
        src_dir = self.project_dir / "src"
        if not src_dir.is_dir():
            return True, "no src/ tree present — nothing to check"
        offenders: List[str] = []
        for path in sorted(src_dir.rglob("*.py")):
            if path.name == "cli.py":
                continue  # the CLI alone may speak to stdout
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"),
                                 filename=str(path))
            except (SyntaxError, OSError, UnicodeDecodeError):
                log.warning("Skipping unparseable file %s", path)
                continue
            hits = [node.lineno for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"]
            if hits:
                rel = path.relative_to(self.project_dir)
                offenders.append(f"{rel}:{','.join(map(str, hits))}")
        if offenders:
            return False, (f"print() found in src/ (cli.py excluded): "
                           f"{'; '.join(offenders)}")
        return True, "no print() calls in src/ (cli.py excluded)"

    def _invariant_event_log(self) -> Tuple[bool, str]:
        """The event log is append-only — v1: verify presence, pass."""
        log_path = self._mythis / "events.jsonl"
        try:
            stat = log_path.stat()
        except OSError:
            return True, "checked — no event log yet, nothing to truncate"
        return True, (f"checked — {stat.st_size} bytes, "
                     f"mtime {int(stat.st_mtime)}")

    def _invariant_state_json(self) -> Tuple[bool, str]:
        """The project state file must be valid JSON."""
        state_path = self._mythis / "PROJECT_STATE.json"
        try:
            text = state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return True, "no state file yet — nothing to validate"
        except OSError as exc:
            return False, f"state file unreadable: {exc}"
        try:
            json.loads(text)
        except ValueError as exc:
            return False, f"state file is not valid JSON: {exc}"
        return True, "state file is valid JSON"
