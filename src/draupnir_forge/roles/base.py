"""base.py — Role base class and registry (slice 9).

A Role is one cognitive mode of the Forge (Skald, Cartographer, ...).
Every role receives a RoleContext and returns a RoleResult. Roles never
talk to each other directly; the Orchestrator (slice 20) sequences them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type


@dataclass
class RoleContext:
    """Everything a role may need. Roles pick what they use."""

    project_dir: str
    config: Any = None            # ForgeConfig (slice 2)
    event_log: Any = None         # EventLog (slice 5)
    state: Any = None             # ProjectState (slice 6)
    budget: Any = None            # Budget (slice 8)
    model_router: Any = None      # ModelRouter (slice 23)
    tools: Any = None             # ToolExecutor (slice 24)
    task: Any = None              # ForgeTask being executed (slice 13)
    artifacts: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RoleResult:
    """What a role produced."""

    ok: bool
    summary: str
    artifacts: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    escalation: Optional[str] = None  # human-readable request, if blocked

    def __post_init__(self) -> None:
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("RoleResult.summary must be a non-empty string")


class Role(ABC):
    """Abstract base for all Forge roles."""

    # Overridden by subclasses.
    name: str = "role"
    purpose: str = ""

    @abstractmethod
    def run(self, ctx: RoleContext) -> RoleResult:
        """Execute the role's cognitive work. Never raises on bad input —
        returns RoleResult(ok=False, ...) instead."""
        ...

    def emit(self, ctx: RoleContext, event_type: Any,
             payload: Dict[str, Any]) -> None:
        """Best-effort event emission; never breaks the role."""
        try:
            if ctx.event_log is not None:
                ctx.event_log.emit(event_type, self.name, payload)
        except Exception:
            pass


class RoleRegistry:
    """Maps role names to Role classes."""

    def __init__(self) -> None:
        self._roles: Dict[str, Type[Role]] = {}

    def register(self, cls: Type[Role]) -> None:
        name = getattr(cls, "name", "")
        if not name or not isinstance(cls, type) or not issubclass(cls, Role):
            raise ValueError(f"Cannot register {cls!r} as a role")
        if name in self._roles:
            raise ValueError(f"Role already registered: {name!r}")
        self._roles[name] = cls

    def create(self, name: str) -> Role:
        try:
            return self._roles[name]()
        except KeyError:
            raise KeyError(f"Unknown role: {name!r}") from None

    def names(self) -> List[str]:
        return sorted(self._roles)

    def __len__(self) -> int:
        return len(self._roles)


# Global registry; role modules register on import.
registry = RoleRegistry()


def register_role(cls: Type[Role]) -> Type[Role]:
    """Class decorator: @register_role on each concrete Role."""
    registry.register(cls)
    return cls
