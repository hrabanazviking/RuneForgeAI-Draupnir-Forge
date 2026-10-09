"""config.py — layered configuration for Draupnir Forge (slice 2).

Configuration sources, weakest to strongest — like rings of bark, each
outer ring shaping what the inner ones began:

    1. ``data/default_config.yaml``          (built-in defaults)
    2. ``~/.draupnir/config.yaml``           (per-user overrides)
    3. ``<project>/.mythis/config.yaml``     (per-project overrides)
    4. ``DRAUPNIR_*`` environment variables  (``__`` separates nesting)
    5. explicit dict passed to :meth:`ForgeConfig.load`

Missing files are silently skipped: a forge that cannot start because a
config file is absent is a forge that cannot be trusted. Invalid values
raise a clear :class:`ValueError` naming the offending key. Validation
rules live in ``data/config_schema.yaml`` — data, never hardcoded.
"""

from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a declared dependency.
    yaml = None  # type: ignore[assignment]

from . import _paths

log = logging.getLogger("draupnir_forge.config")

DEFAULT_CONFIG_FILE = "default_config.yaml"
SCHEMA_FILE = "config_schema.yaml"
USER_CONFIG_RELATIVE = os.path.join(".draupnir", "config.yaml")
PROJECT_CONFIG_RELATIVE = os.path.join(".mythis", "config.yaml")
ENV_PREFIX = "DRAUPNIR_"
ENV_NEST_SEPARATOR = "__"

_MISSING = object()


# ---------------------------------------------------------------------------
# YAML loading helpers (never crash on user files)
# ---------------------------------------------------------------------------

def _load_yaml_file(path: Path) -> Dict[str, Any]:
    """Load a YAML mapping from *path*, returning ``{}`` on any problem.

    Missing files are silently skipped. Unreadable or malformed files
    produce a warning and are skipped too — user config must never be
    able to sink the Forge.
    """
    try:
        if not path.is_file():
            return {}
    except OSError:
        return {}
    if yaml is None:
        log.warning("PyYAML unavailable; skipping config file %s", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except Exception as exc:
        log.warning("Ignoring unreadable config file %s: %s", path, exc)
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        log.warning("Ignoring config file %s: top level must be a mapping", path)
        return {}
    return data


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: Dict[str, Any], overlay: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a new dict that is *base* with *overlay* merged in deeply.

    Nested mappings merge key by key; any other value (or a type change)
    is replaced outright by the overlay's value. Neither input is mutated.
    """
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, Mapping)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _flatten(mapping: Mapping[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Flatten a nested mapping to dotted keys, keeping only leaf values."""
    flat: Dict[str, Any] = {}
    for key, value in mapping.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            flat.update(_flatten(value, dotted))
        else:
            flat[dotted] = value
    return flat


def _unflatten(dotted: Mapping[str, Any]) -> Dict[str, Any]:
    """Expand dotted keys (``{"a.b": 1}``) into nested mappings."""
    nested: Dict[str, Any] = {}
    for dotted_key, value in dotted.items():
        node = nested
        parts = str(dotted_key).split(".")
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
    return nested


def _normalize_overrides(overrides: Mapping[str, Any]) -> Dict[str, Any]:
    """Accept an overrides mapping with nested and/or dotted keys."""
    if not isinstance(overrides, Mapping):
        raise ValueError(
            f"Config overrides must be a mapping, got {type(overrides).__name__}."
        )
    nested: Dict[str, Any] = {}
    dotted: Dict[str, Any] = {}
    for key, value in overrides.items():
        if "." in str(key):
            dotted[str(key)] = value
        else:
            nested[key] = value
    return _deep_merge(nested, _unflatten(dotted))


def _env_overrides() -> Dict[str, Any]:
    """Build an overrides mapping from ``DRAUPNIR_*`` environment variables.

    ``DRAUPNIR_AUTONOMY=deep`` sets ``autonomy``; double underscores nest:
    ``DRAUPNIR_MODEL__NAME=gpt-4o`` sets ``model.name``. The reserved
    ``DRAUPNIR_DATA_DIR`` variable (see ``_paths``) is not a config key
    and is skipped. Values stay strings here; validation coerces them.
    """
    dotted: Dict[str, str] = {}
    for var, value in os.environ.items():
        if not var.startswith(ENV_PREFIX) or var == _paths.DATA_DIR_ENV_VAR:
            continue
        body = var[len(ENV_PREFIX):]
        if not body:
            continue
        parts = [p for p in body.lower().split(ENV_NEST_SEPARATOR) if p]
        if not parts:
            continue
        dotted[".".join(parts)] = value
    return _unflatten(dotted)


# ---------------------------------------------------------------------------
# Validation (rules come from data/config_schema.yaml)
# ---------------------------------------------------------------------------

def _coerce(value: Any, type_name: str, key: str) -> Any:
    """Coerce *value* to the schema type, raising ValueError on failure.

    Stringly sources (environment variables) are parsed strictly:
    ``"false"`` becomes ``False``, never the truthy string.
    """
    location = f"config key {key!r}"
    if type_name == "str":
        if isinstance(value, str):
            return value
        raise ValueError(f"{location} must be a string, got {type(value).__name__}.")
    if type_name == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return True
            if lowered in ("0", "false", "no", "off"):
                return False
        raise ValueError(
            f"{location} must be a boolean (true/false), got {value!r}."
        )
    if type_name == "int":
        if isinstance(value, bool):
            raise ValueError(f"{location} must be an integer, got {value!r}.")
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip(), 10)
            except ValueError:
                pass
        raise ValueError(f"{location} must be an integer, got {value!r}.")
    if type_name == "float":
        if isinstance(value, bool):
            raise ValueError(f"{location} must be a number, got {value!r}.")
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                pass
        raise ValueError(f"{location} must be a number, got {value!r}.")
    raise ValueError(f"{location}: unknown schema type {type_name!r}.")


def _validate(merged: Dict[str, Any], schema: Mapping[str, Any]) -> None:
    """Validate the merged config in place (coercing where the schema allows).

    Raises:
        ValueError: On a missing key, wrong type, disallowed value, or
            out-of-range number. The message always names the key.
    """
    constraints = schema.get("constraints", {})
    if not isinstance(constraints, Mapping):
        raise ValueError("Config schema is malformed: 'constraints' must be a mapping.")
    for dotted_key, rules in constraints.items():
        if not isinstance(rules, Mapping):
            raise ValueError(f"Config schema is malformed at {dotted_key!r}.")
        node: Any = merged
        parts = str(dotted_key).split(".")
        for part in parts[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if not isinstance(node, dict) or parts[-1] not in node:
            raise ValueError(
                f"Config error: required key {dotted_key!r} is missing. "
                "The built-in defaults may be incomplete."
            )
        coerced = _coerce(node[parts[-1]], str(rules.get("type", "str")), dotted_key)
        allowed = rules.get("allowed")
        if allowed is not None and coerced not in allowed:
            raise ValueError(
                f"Config error: {dotted_key!r} must be one of "
                f"{list(allowed)}, got {coerced!r}."
            )
        if rules.get("non_empty") and coerced == "":
            raise ValueError(f"Config error: {dotted_key!r} must not be empty.")
        if "min" in rules and coerced < rules["min"]:
            raise ValueError(
                f"Config error: {dotted_key!r} must be >= {rules['min']}, "
                f"got {coerced!r}."
            )
        if "max" in rules and coerced > rules["max"]:
            raise ValueError(
                f"Config error: {dotted_key!r} must be <= {rules['max']}, "
                f"got {coerced!r}."
            )
        node[parts[-1]] = coerced
    # Unknown keys are tolerated (forward compatibility) but announced.
    known = {str(k) for k in constraints}
    for dotted_key in _flatten(merged):
        if dotted_key not in known:
            log.warning("Unknown config key %r is not in the schema; ignoring.", dotted_key)


# ---------------------------------------------------------------------------
# The config itself
# ---------------------------------------------------------------------------

@dataclass
class ForgeConfig:
    """Layered, validated configuration for Draupnir Forge.

    Build with :meth:`load`, which merges the five layers (defaults <
    user file < project file < environment < explicit overrides) and
    validates the result. Read values with dotted :meth:`get`, or take a
    snapshot with :meth:`to_dict`.
    """

    _data: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def load(
        cls,
        project_dir: Optional[Union[str, Path]] = None,
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> "ForgeConfig":
        """Load and validate the layered configuration.

        Args:
            project_dir: Project root; ``<project>/.mythis/config.yaml``
                is read when given. ``None`` skips the project layer.
            overrides: Explicit mapping (nested and/or dotted keys) with
                the highest precedence.

        Returns:
            A validated :class:`ForgeConfig`.

        Raises:
            ValueError: If a value fails schema validation, or a
                built-in data file is missing/broken.
        """
        merged: Dict[str, Any] = {}
        # Ring 1: built-in defaults (a broken install deserves a loud error).
        merged = _deep_merge(merged, _paths.load_data_yaml(DEFAULT_CONFIG_FILE))
        # Ring 2: per-user file, silently skipped when absent.
        home = Path(os.path.expanduser("~"))
        merged = _deep_merge(merged, _load_yaml_file(home / USER_CONFIG_RELATIVE))
        # Ring 3: per-project file, silently skipped when absent.
        if project_dir is not None:
            project_file = Path(project_dir) / PROJECT_CONFIG_RELATIVE
            merged = _deep_merge(merged, _load_yaml_file(project_file))
        # Ring 4: environment.
        merged = _deep_merge(merged, _env_overrides())
        # Ring 5: explicit overrides.
        if overrides is not None:
            merged = _deep_merge(merged, _normalize_overrides(overrides))
        _validate(merged, _paths.load_data_yaml(SCHEMA_FILE))
        return cls(_data=merged)

    def get(self, key: str, default: Any = _MISSING) -> Any:
        """Return the value at a dotted *key* (e.g. ``"model.provider"``).

        Raises:
            KeyError: If the key is absent and no *default* was given.
        """
        node: Any = self._data
        for part in str(key).split("."):
            if not isinstance(node, dict) or part not in node:
                if default is _MISSING:
                    raise KeyError(f"Unknown config key: {key!r}")
                return default
            node = node[part]
        return node

    def to_dict(self) -> Dict[str, Any]:
        """Return a deep copy of the configuration as nested dicts."""
        return copy.deepcopy(self._data)
