"""roles package — imports all role modules so @register_role fires.

Importing this package registers every concrete Role with the global
registry in draupnir_forge.roles.base. Add new role modules here.
"""

from __future__ import annotations

from . import architect  # noqa: F401
from . import auditor  # noqa: F401
from . import base  # noqa: F401
from . import cartographer  # noqa: F401
from . import planner  # noqa: F401
from . import skald  # noqa: F401
from . import worker  # noqa: F401

__all__ = [
    "architect",
    "auditor",
    "base",
    "cartographer",
    "planner",
    "skald",
    "worker",
]
