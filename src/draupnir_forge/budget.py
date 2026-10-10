"""Slice 8 — Token/cost budget tracker.

The Forge burns tokens like a smithy burns coal; this module is the
coal-weigher. A ``Budget`` caps a project run by total tokens and by USD
cost, prices every charge against the per-model price table in
``data/model_prices.yaml``, persists its ledger atomically to
``<project>/.mythis/budget.json``, and refuses — atomically — any charge
that would blow a cap.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


class BudgetExhausted(Exception):
    """Raised by ``Budget.charge`` when a charge would exceed a cap.

    The charge is NOT applied when this is raised: ``charge`` is atomic.
    """


def _repo_root() -> Path:
    """Locate the repository root from this file's location.

    ``budget.py`` lives at ``<root>/src/draupnir_forge/budget.py``, so three
    parents up is the root. No absolute paths are baked in; the module works
    from a source checkout or an installed package alike.
    """
    return Path(__file__).resolve().parents[2]


def _find_price_file() -> Path | None:
    """Locate ``data/model_prices.yaml`` without assuming a cwd.

    Checks, in order: the ``DRAUPNIR_PRICE_FILE`` env override, the repo
    ``data/`` directory next to this package, and a ``data/`` directory
    under the current working directory. Returns ``None`` when nothing is
    found so the caller can fall back to embedded estimates.
    """
    override = os.environ.get("DRAUPNIR_PRICE_FILE")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate
    for candidate in (
        _repo_root() / "data" / "model_prices.yaml",
        Path.cwd() / "data" / "model_prices.yaml",
    ):
        if candidate.is_file():
            return candidate
    return None


# Embedded fallback estimates (per-1K USD). Used ONLY when the YAML price
# file cannot be read — the YAML file is the source of truth. Kept minimal
# so the Forge stays crashproof instead of dying on a missing data file.
_FALLBACK_PRICES: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "ollama": {"input": 0.0, "output": 0.0},
    "local": {"input": 0.0, "output": 0.0},
}


def load_model_prices() -> dict[str, dict[str, float]]:
    """Load the per-1K-token USD price table.

    Reads ``data/model_prices.yaml`` (see ``_find_price_file``); on any read
    or parse failure falls back to ``_FALLBACK_PRICES`` so budgeting never
    crashes the run. Values are normalized to ``{"input": float,
    "output": float}``; malformed entries are dropped.
    """
    prices: dict[str, dict[str, float]] = {}
    price_file = _find_price_file()
    if price_file is not None:
        try:
            raw = yaml.safe_load(price_file.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                for model, entry in raw.items():
                    if isinstance(entry, dict) and "input" in entry and "output" in entry:
                        try:
                            prices[str(model)] = {
                                "input": float(entry["input"]),
                                "output": float(entry["output"]),
                            }
                        except (TypeError, ValueError):
                            continue
        except (OSError, yaml.YAMLError):
            prices = {}
    if not prices:
        prices = {k: dict(v) for k, v in _FALLBACK_PRICES.items()}
    return prices


def _budget_file(project_dir: Path) -> Path:
    return project_dir / ".mythis" / "budget.json"


def _read_ledger(path: Path) -> dict[str, Any]:
    """Read the persisted ledger; corrupt or missing -> fresh zero ledger."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("ledger root is not an object")
        return {
            "tokens_used": int(data.get("tokens_used", 0)),
            "cost_usd": float(data.get("cost_usd", 0.0)),
            "tasks_completed": int(data.get("tasks_completed", 0)),
            "warned": bool(data.get("warned", False)),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"tokens_used": 0, "cost_usd": 0.0, "tasks_completed": 0, "warned": False}


def _write_ledger_atomic(path: Path, ledger: dict[str, Any]) -> None:
    """Write the ledger atomically: temp file in the same dir, then replace.

    A crash mid-write can never leave a half-written budget.json behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".budget-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(ledger, handle, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class Budget:
    """Token and USD budget for one project run.

    Args:
        project_dir: Project root; the ledger lives at
            ``<project_dir>/.mythis/budget.json``.
        max_tokens: Hard cap on total tokens (input + output).
        max_cost_usd: Hard cap on total USD cost.

    The ledger (tokens used, cost, tasks completed) is loaded from disk on
    init and persisted after every mutation. A corrupt ledger file is
    treated as a fresh zero budget rather than a fatal error.
    """

    def __init__(
        self,
        project_dir: str | Path,
        max_tokens: int,
        max_cost_usd: float,
    ) -> None:
        if max_tokens < 0:
            raise ValueError("max_tokens must be non-negative")
        if max_cost_usd < 0:
            raise ValueError("max_cost_usd must be non-negative")
        self.project_dir = Path(project_dir)
        self.max_tokens = int(max_tokens)
        self.max_cost_usd = float(max_cost_usd)
        self._prices = load_model_prices()
        self._file = _budget_file(self.project_dir)
        ledger = _read_ledger(self._file)
        self._tokens_used: int = ledger["tokens_used"]
        self._cost_usd: float = ledger["cost_usd"]
        self._tasks_completed: int = ledger["tasks_completed"]
        # One-shot near-exhaustion flag: True once check_warning() has
        # fired. Persisted in the ledger so a re-created Budget does not
        # re-warn after a restart.
        self.warned: bool = ledger["warned"]

    # -- pricing ---------------------------------------------------------
    def price_for(self, model: str) -> dict[str, float]:
        """Return the per-1K USD price entry for ``model``.

        Raises:
            ValueError: If the model has no price entry. Unknown models
                never silently price at zero.
        """
        try:
            return self._prices[model]
        except KeyError:
            known = ", ".join(sorted(self._prices))
            raise ValueError(
                f"unknown model {model!r}: no price entry in model_prices.yaml "
                f"(known models: {known})"
            ) from None

    # -- charging ---------------------------------------------------------
    def charge(self, model: str, in_tokens: int, out_tokens: int) -> float:
        """Charge a model call against the budget.

        Args:
            model: Model name, must exist in the price table.
            in_tokens: Prompt tokens consumed (non-negative).
            out_tokens: Completion tokens consumed (non-negative).

        Returns:
            The USD cost of this charge.

        Raises:
            ValueError: Unknown model or negative token counts.
            BudgetExhausted: If the charge would exceed either cap. The
                charge is NOT applied — the ledger is untouched (atomic).
        """
        if in_tokens < 0 or out_tokens < 0:
            raise ValueError("token counts must be non-negative")
        price = self.price_for(model)
        cost = (in_tokens / 1000.0) * price["input"] + (out_tokens / 1000.0) * price[
            "output"
        ]
        new_tokens = self._tokens_used + in_tokens + out_tokens
        new_cost = self._cost_usd + cost
        if new_tokens > self.max_tokens:
            raise BudgetExhausted(
                f"token budget exhausted: {new_tokens:,} would exceed "
                f"cap of {self.max_tokens:,}"
            )
        if new_cost > self.max_cost_usd:
            raise BudgetExhausted(
                f"cost budget exhausted: ${new_cost:,.4f} would exceed "
                f"cap of ${self.max_cost_usd:,.2f}"
            )
        self._tokens_used = new_tokens
        self._cost_usd = new_cost
        self._persist()
        return cost

    def record_task_complete(self) -> int:
        """Record one completed task; returns the new total."""
        self._tasks_completed += 1
        self._persist()
        return self._tasks_completed

    # -- introspection -----------------------------------------------------
    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def cost_usd(self) -> float:
        return self._cost_usd

    @property
    def tasks_completed(self) -> int:
        return self._tasks_completed

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self._tokens_used)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.max_cost_usd - self._cost_usd)

    def exceeded(self) -> bool:
        """True when either cap has been reached or passed."""
        return self._tokens_used >= self.max_tokens or self._cost_usd >= self.max_cost_usd

    def is_near_exhausted(self, threshold: float = 0.8) -> bool:
        """True when token usage or cost reaches ``threshold`` of its cap.

        Args:
            threshold: Fraction (0..1) of a cap that counts as
                near-exhausted. Default 0.8 (80%).

        A zero (or otherwise missing) cap never reports near-exhausted on
        that dimension — only the live cap guards.
        """
        max_tokens = self.max_tokens or 0
        max_cost = self.max_cost_usd or 0.0
        near_tokens = max_tokens > 0 and self._tokens_used / max_tokens >= threshold
        near_cost = max_cost > 0 and self._cost_usd / max_cost >= threshold
        return bool(near_tokens or near_cost)

    def check_warning(self, threshold: float = 0.8) -> bool:
        """One-shot near-exhaustion signal for the orchestrator.

        Returns True exactly once — on the first call that finds the
        budget near-exhausted (it sets ``warned`` and persists it). Later
        calls return False, so the orchestrator emits one warning and no
        more. Returns False while the budget is comfortably below the
        threshold.
        """
        if self.is_near_exhausted(threshold):
            if not self.warned:
                self.warned = True
                self._persist()
                return True
        return False

    def _persist(self) -> None:
        _write_ledger_atomic(
            self._file,
            {
                "tokens_used": self._tokens_used,
                "cost_usd": self._cost_usd,
                "tasks_completed": self._tasks_completed,
                "warned": self.warned,
            },
        )

    # -- reporting ---------------------------------------------------------
    def report(self) -> str:
        """Human-readable multi-line summary of budget state."""
        tok_pct = (self._tokens_used / self.max_tokens * 100) if self.max_tokens else 0.0
        usd_pct = (
            (self._cost_usd / self.max_cost_usd * 100) if self.max_cost_usd else 0.0
        )
        status = "EXHAUSTED" if self.exceeded() else "within budget"
        lines = [
            f"Budget: {self._tokens_used:,}/{self.max_tokens:,} tokens ({tok_pct:.1f}%), "
            f"${self._cost_usd:,.2f}/${self.max_cost_usd:,.2f}, "
            f"{self._tasks_completed} tasks completed",
            f"Remaining: {self.remaining_tokens:,} tokens, ${self.remaining_usd:,.2f}",
            f"Status: {status}",
        ]
        if self.is_near_exhausted():
            lines.append("WARNING: budget near exhaustion")
        return "\n".join(lines)
