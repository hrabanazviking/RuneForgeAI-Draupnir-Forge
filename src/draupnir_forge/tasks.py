"""tasks.py — Forge task model, re-exported for the Orchestrator.

The canonical :class:`ForgeTask` dataclass lives in
``draupnir_forge.roles.planner`` (slice 13); this module re-exports it
so the Orchestrator (slice 20) and other consumers can import it from
a stable, role-agnostic location.
"""

from __future__ import annotations

from draupnir_forge.roles.planner import ForgeTask

__all__ = ["ForgeTask"]
