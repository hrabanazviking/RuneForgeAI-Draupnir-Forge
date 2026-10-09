"""_paths.py — internal helpers for locating the Forge's data files.

Not part of the public API (leading underscore). Both ``config.py`` and
``log.py`` need the YAML data files that live under the repository's
``data/`` directory, without depending on the current working directory
or on a particular install layout — so the search logic lives here once,
like a raven that always knows the way home.

Search order for a data file:
  1. The ``DRAUPNIR_DATA_DIR`` environment variable (explicit override).
  2. ``importlib.resources`` inside the installed package (pip layout).
  3. Walking upwards from this file looking for a sibling ``data/``
     directory (source-checkout layout: ``<repo>/data/<name>``).
  4. ``./data/<name>`` relative to the current working directory.

All helpers return ``None`` / empty dict instead of raising — callers
decide how to handle a miss, and the Forge never crashes over a path.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a declared dependency.
    yaml = None  # type: ignore[assignment]

log = logging.getLogger("draupnir_forge.paths")

DATA_DIR_ENV_VAR = "DRAUPNIR_DATA_DIR"


def find_data_file(filename: str) -> Optional[Path]:
    """Return the path to a data file, or ``None`` if it cannot be found.

    Searches the locations described in the module docstring, in order.
    Never raises.
    """
    # 1. Explicit override via environment.
    try:
        override_dir = os.environ.get(DATA_DIR_ENV_VAR)
        if override_dir:
            candidate = Path(override_dir) / filename
            if candidate.is_file():
                return candidate
    except Exception as exc:  # Huginn reports, never panics.
        log.warning("Could not check %s: %s", DATA_DIR_ENV_VAR, exc)

    # 2. Installed package resources (pip layout).
    try:
        from importlib.resources import files as resource_files

        candidate = resource_files("draupnir_forge") / "data" / filename
        if candidate.is_file():
            return Path(str(candidate))
    except Exception:
        pass  # Source checkout is the common case; keep scouting.

    # 3. Walk upwards from this file (source-checkout layout).
    try:
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / "data" / filename
            if candidate.is_file():
                return candidate
    except Exception as exc:
        log.warning("Could not search upwards for %s: %s", filename, exc)

    # 4. Last resort: relative to the current working directory.
    try:
        candidate = Path.cwd() / "data" / filename
        if candidate.is_file():
            return candidate
    except Exception as exc:
        log.warning("Could not check ./data/%s: %s", filename, exc)

    return None


def load_data_yaml(filename: str) -> Dict[str, Any]:
    """Load a YAML data file found by :func:`find_data_file`.

    Raises:
        ValueError: If the file cannot be found or cannot be parsed.
            A missing *built-in* data file means the installation itself
            is broken, so a loud, clear error is the honest response.
    """
    path = find_data_file(filename)
    if path is None:
        raise ValueError(
            f"Built-in data file {filename!r} not found. "
            f"Set {DATA_DIR_ENV_VAR} to the directory holding it."
        )
    if yaml is None:
        raise ValueError("PyYAML is required to read data files.")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except Exception as exc:
        raise ValueError(f"Could not parse data file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Data file {path} must hold a YAML mapping.")
    return data
