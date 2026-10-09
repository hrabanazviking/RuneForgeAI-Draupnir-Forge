"""RuneForgeAI - Draupnir Forge.

One intent. A thousand hammer blows. One coherent system.

Recursive Mythic Engineering for autonomous AI-native software development:
the human owns the definition, the Forge handles the construction.
"""

from __future__ import annotations

__version__ = "0.1.0"

# The Twelve Laws of the Forge (DRAUPNIR_FORGE_SPEC.md §38).
# Every subsystem must honor these; the Auditor checks drift against them.
FORGE_LAWS = (
    "Human intent outranks model convenience.",
    "Reality outranks generated explanation.",
    "Architecture outranks patch accumulation.",
    "Definition precedes delegation.",
    "No critical system truth should live only in one mind or one context window.",
    "Every subsystem must have an owner and a boundary.",
    "Every important change must leave evidence.",
    "A model may implement its own work, but it may not be the sole judge of that work.",
    "Failure should produce knowledge, not blind repetition.",
    "The system should ask the human for judgment, not for permission to perform obvious engineering work.",
    "Continuity is a first-class engineering requirement.",
    "The simpler the surface becomes, the more disciplined the hidden machinery must be.",
)

__all__ = ["__version__", "FORGE_LAWS"]
