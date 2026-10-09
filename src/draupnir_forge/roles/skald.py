"""skald.py — Skald role: intent interpreter (slice 10).

The Skald hears the human's raw intent and sings it back as an explicit
Vision: the goal, what matters most, what is out of scope, how success is
judged, and what remains unclear. Rule-based v1 (no model required);
prompt templates for future model-driven interpretation live in
``data/skald_prompts.yaml`` — data, never hardcoded.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from draupnir_forge.roles.base import Role, RoleContext, RoleResult, register_role

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Keyword rules — module data for the rule-based v1 interpreter.
# ---------------------------------------------------------------------------

# Words that mark a sentence as stating a priority.
PRIORITY_WORDS = ("must", "important", "priority", "critical", "essential",
                  "key requirement")

# Words/phrases that mark a sentence as stating a non-goal.
NON_GOAL_WORDS = ("not ", "never", "exclude", "excluded", "out of scope",
                  "no ", "avoid", "non-goal", "won't", "will not")

# Words that mark a sentence as stating a success criterion.
SUCCESS_WORDS = ("should", "success", "done when", "verify", "acceptance",
                 "must pass", "definition of done")

# Vague terms that flag a sentence as ambiguous.
VAGUE_TERMS = ("etc", "stuff", "things", "somehow", "various", "tbd",
               "unknown", "whatever", "misc", "miscellaneous")

# A sentence is "substantive" if it has at least this many word chars.
_MIN_SUBSTANTIVE_LEN = 12


@dataclass
class Vision:
    """The Skald's explicit reading of human intent."""

    goal: str = ""
    priorities: List[str] = field(default_factory=list)
    non_goals: List[str] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)
    ambiguities: List[str] = field(default_factory=list)


def _split_sentences(text: str) -> List[str]:
    """Split prose into sentences on . ! ? boundaries.

    Simple but sturdy: keeps abbreviations from shattering the goal.
    """
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])", text)
    return [p.strip() for p in parts if p.strip()]


def _contains_any(sentence: str, words: tuple) -> bool:
    lowered = sentence.lower()
    return any(w in lowered for w in words)


def _is_ambiguous(sentence: str) -> bool:
    """A sentence is ambiguous if it asks, hedges, or waves its hands."""
    lowered = sentence.lower()
    if "?" in sentence:
        return True
    if "whether" in lowered:
        return True
    if re.search(r"\bor\b", lowered) and "either" in lowered:
        return True
    if re.search(r"\b(or)\b", lowered) and len(sentence.split()) < 12:
        # short either/or clauses ("use X or Y") are unresolved choices
        return True
    return _contains_any(sentence, VAGUE_TERMS)


def interpret(text: str) -> Vision:
    """Interpret raw human intent into an explicit Vision (rule-based v1).

    - goal: first substantive sentence.
    - priorities: sentences mentioning must/important/priority/critical/...
    - non_goals: sentences with not/never/exclude/out of scope/...
    - success_criteria: sentences with should/success/done when/verify/...
    - ambiguities: questions, whether/or clauses, vague terms.

    A sentence may appear in several lists; the Vision is a map, not a
    partition. Never raises — empty input yields an empty Vision.
    """
    vision = Vision()
    if not text or not text.strip():
        return vision

    sentences = _split_sentences(text)
    if not sentences:
        return vision

    # The goal is the first substantive sentence — the spearhead of intent.
    for sentence in sentences:
        if len(sentence) >= _MIN_SUBSTANTIVE_LEN and "?" not in sentence:
            vision.goal = sentence
            break
    if not vision.goal:
        vision.goal = sentences[0]

    for sentence in sentences:
        if _contains_any(sentence, PRIORITY_WORDS):
            vision.priorities.append(sentence)
        if _contains_any(sentence, NON_GOAL_WORDS):
            vision.non_goals.append(sentence)
        if _contains_any(sentence, SUCCESS_WORDS):
            vision.success_criteria.append(sentence)
        if _is_ambiguous(sentence):
            vision.ambiguities.append(sentence)

    return vision


def _locate_data_file(name: str) -> Optional[str]:
    """Find ``data/<name>`` by walking up from this module's directory.

    Keeps the role file-location agnostic: it works from a checkout,
    an installed package, or a relocated tree.
    """
    cur = os.path.dirname(os.path.abspath(__file__))
    while True:
        candidate = os.path.join(cur, "data", name)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def load_prompts() -> Dict[str, Any]:
    """Load Skald prompt templates from ``data/skald_prompts.yaml``.

    Returns an empty dict (never raises) if the file is missing or
    unreadable — the rule-based v1 interpreter does not need it.
    """
    path = _locate_data_file("skald_prompts.yaml")
    if path is None:
        _LOG.warning("skald_prompts.yaml not found; continuing without "
                     "model prompt templates")
        return {}
    try:
        import yaml
    except ImportError:
        _LOG.warning("PyYAML unavailable; cannot load skald prompts")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # corrupt YAML must not break the role
        _LOG.warning("failed to load skald prompts from %s: %s", path, exc)
        return {}


def _render_markdown(vision: Vision) -> str:
    """Render a Vision as the SYSTEM_VISION.md document."""
    lines = [
        "# System Vision",
        "",
        "_Sung by the Skald — rule-based v1 interpretation of human intent._",
        "",
        "## Goal",
        "",
        vision.goal or "_No goal could be discerned._",
        "",
        "## Priorities",
        "",
    ]
    if vision.priorities:
        lines.extend(f"- {p}" for p in vision.priorities)
    else:
        lines.append("_None identified._")
    lines += ["", "## Non-goals", ""]
    if vision.non_goals:
        lines.extend(f"- {n}" for n in vision.non_goals)
    else:
        lines.append("_None identified._")
    lines += ["", "## Success criteria", ""]
    if vision.success_criteria:
        lines.extend(f"- {s}" for s in vision.success_criteria)
    else:
        lines.append("_None identified._")
    lines += ["", "## Open ambiguities", ""]
    if vision.ambiguities:
        lines.extend(f"- {a}" for a in vision.ambiguities)
    else:
        lines.append("_None identified._")
    lines.append("")
    return "\n".join(lines)


def _read_goal_text(ctx: RoleContext) -> str:
    """Pull raw goal text from the task or from prior artifacts."""
    task_goal = getattr(ctx.task, "goal", None)
    if isinstance(task_goal, str) and task_goal.strip():
        return task_goal
    artifact_goal = ctx.artifacts.get("goal_text")
    if isinstance(artifact_goal, str) and artifact_goal.strip():
        return artifact_goal
    return ""


@register_role
class Skald(Role):
    """The Skald interprets human intent into an explicit Vision."""

    name = "skald"
    purpose = "interprets human intent into explicit vision"

    def run(self, ctx: RoleContext) -> RoleResult:
        """Interpret goal text and write ``.mythis/SYSTEM_VISION.md``."""
        goal_text = _read_goal_text(ctx)
        if not goal_text:
            return RoleResult(
                ok=False,
                summary="skald found no goal text (task.goal / "
                        "artifacts['goal_text'] empty)",
                escalation="A goal statement is required before the Skald "
                           "can interpret intent.",
            )

        try:
            vision = interpret(goal_text)
            mythis_dir = os.path.join(ctx.project_dir, ".mythis")
            os.makedirs(mythis_dir, exist_ok=True)
            vision_path = os.path.join(mythis_dir, "SYSTEM_VISION.md")
            with open(vision_path, "w", encoding="utf-8") as handle:
                handle.write(_render_markdown(vision))
        except OSError as exc:
            return RoleResult(
                ok=False,
                summary=f"skald could not write SYSTEM_VISION.md: {exc}",
                escalation=f"Check that {ctx.project_dir} is writable.",
            )
        except Exception as exc:  # never let the role crash the forge
            _LOG.warning("skald failed: %s", exc)
            return RoleResult(
                ok=False,
                summary=f"skald failed unexpectedly: {exc}",
            )

        self.emit(ctx, "VISION_INTERPRETED",
                  {"vision_path": vision_path,
                   "goal": vision.goal,
                   "ambiguity_count": len(vision.ambiguities)})
        summary = (f"vision interpreted: {len(vision.priorities)} priorities, "
                   f"{len(vision.non_goals)} non-goals, "
                   f"{len(vision.success_criteria)} success criteria, "
                   f"{len(vision.ambiguities)} ambiguities")
        return RoleResult(
            ok=True,
            summary=summary,
            artifacts={"vision": vision, "vision_path": vision_path},
        )
