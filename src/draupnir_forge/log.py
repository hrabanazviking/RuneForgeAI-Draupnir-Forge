"""log.py — logging infrastructure for Draupnir Forge (slice 3).

One root logger (``draupnir_forge``) carries a console handler; child
loggers from :func:`get_logger` inherit it. File logging is opt-in per
project via :func:`configure_file_logging`, which attaches a rotating
file handler at ``<project>/.mythis/logs/forge.log``.

Laws honored here: no ``print()`` anywhere in library code, and the
Forge never raises because a log directory is unwritable — it falls back
to console-only and says so out loud. Format and rotation settings live
in ``data/logging.yaml`` (data, never hardcoded).
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Union

from . import _paths

ROOT_LOGGER_NAME = "draupnir_forge"
LOGGING_DATA_FILE = "logging.yaml"

_CONSOLE_READY_ATTR = "_draupnir_console_ready"

_settings_cache: Optional[dict] = None


def _settings() -> dict:
    """Return the logging settings, falling back to sane built-ins."""
    global _settings_cache
    if _settings_cache is None:
        try:
            _settings_cache = _paths.load_data_yaml(LOGGING_DATA_FILE)
        except Exception:
            # The forge must log even if its own settings are missing.
            _settings_cache = {}
    return _settings_cache


def _parse_level(value: object, fallback: int = logging.WARNING) -> int:
    """Parse a logging level from a name or number; never raises."""
    try:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            level = logging.getLevelName(value.strip().upper())
            if isinstance(level, int):
                return level
    except Exception:
        pass
    return fallback


class ForgeFormatter(logging.Formatter):
    """Log formatter for the Forge.

    Uses the canonical format from ``data/logging.yaml``
    (``%(asctime)s [%(levelname)s] %(name)s: %(message)s``). When a record
    carries a ``role`` field (see :func:`bind_role`), the role is shown
    in brackets right after the logger name.
    """

    def __init__(self) -> None:
        settings = _settings()
        self._base_format = str(
            settings.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        self._datefmt = settings.get("date_format")
        self._role_formatters: dict[str, logging.Formatter] = {}
        super().__init__(fmt=self._base_format, datefmt=self._datefmt)

    def format(self, record: logging.LogRecord) -> str:
        role = getattr(record, "role", "")
        if not role:
            return super().format(record)
        formatter = self._role_formatters.get(role)
        if formatter is None:
            role_format = self._base_format.replace("%(name)s", f"%(name)s [{role}]", 1)
            formatter = logging.Formatter(fmt=role_format, datefmt=self._datefmt)
            self._role_formatters[role] = formatter
        return formatter.format(record)


def _ensure_console_handler() -> None:
    """Attach the stderr console handler to the root logger, exactly once."""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    if getattr(root, _CONSOLE_READY_ATTR, False):
        return
    settings = _settings()
    handler = logging.StreamHandler()  # stderr, the skald's voice
    handler.setFormatter(ForgeFormatter())
    handler.setLevel(_parse_level(settings.get("console_level"), logging.WARNING))
    root.addHandler(handler)
    if root.level > logging.DEBUG:
        root.setLevel(logging.DEBUG)
    setattr(root, _CONSOLE_READY_ATTR, True)


def get_logger(name: str, verbose: bool = False) -> logging.Logger:
    """Return the Forge logger for *name* (``draupnir_forge.<name>``).

    The console handler logs at WARNING by default, or DEBUG when
    *verbose* is true. Calling this twice for the same name never
    duplicates handlers.
    """
    _ensure_console_handler()
    logger = logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
    settings = _settings()
    level_name = settings.get("verbose_level", "DEBUG") if verbose else settings.get(
        "console_level", "WARNING"
    )
    logger.setLevel(_parse_level(level_name, logging.WARNING))
    return logger


def configure_file_logging(
    project_dir: Union[str, os.PathLike],
    max_bytes: Optional[int] = None,
    backup_count: Optional[int] = None,
) -> bool:
    """Attach a rotating file handler at ``<project>/.mythis/logs/forge.log``.

    Args:
        project_dir: Project root owning the log file.
        max_bytes: Rotation size; defaults to the data file's setting.
        backup_count: Rotated backups kept; defaults to the data file's.

    Returns:
        True when the file handler is active, False when the log
        directory is unwritable — in which case the Forge keeps running
        on console logging alone and emits a warning. Never raises.
    """
    root = logging.getLogger(ROOT_LOGGER_NAME)
    settings = _settings()
    try:
        log_dir = Path(project_dir) / str(settings.get("log_subdir", ".mythis/logs"))
        log_path = log_dir / str(settings.get("log_filename", "forge.log"))
        # A handler for this exact file already attached? Then we are done.
        for existing in root.handlers:
            if isinstance(existing, RotatingFileHandler) and getattr(
                existing, "baseFilename", ""
            ) == str(log_path):
                return True
        log_dir.mkdir(parents=True, exist_ok=True)
        rotation = settings.get("rotation", {}) or {}
        handler = RotatingFileHandler(
            str(log_path),
            maxBytes=max_bytes if max_bytes is not None else int(rotation.get("max_bytes", 1048576)),
            backupCount=backup_count if backup_count is not None else int(rotation.get("backup_count", 3)),
            encoding="utf-8",
        )
        handler.setLevel(_parse_level(settings.get("file_level"), logging.DEBUG))
        handler.setFormatter(ForgeFormatter())
        root.addHandler(handler)
        if root.level > logging.DEBUG:
            root.setLevel(logging.DEBUG)
        return True
    except Exception as exc:
        # The log scribe's desk is broken; the saga continues by voice.
        logging.getLogger(f"{ROOT_LOGGER_NAME}.log").warning(
            "File logging unavailable for %s (%s); continuing with console only.",
            project_dir,
            exc,
        )
        return False


def bind_role(logger: logging.Logger, role_name: str) -> logging.LoggerAdapter:
    """Wrap *logger* so every record carries a ``role`` field.

    The role appears in formatted output as ``[RoleName]`` after the
    logger name, and is available as ``record.role`` for machine readers.
    The name is sanitized so it can never break the format string.
    """
    safe = "".join(
        ch for ch in str(role_name) if ch.isalnum() or ch in (" ", "-", "_")
    ).strip()
    if not safe:
        safe = "unknown"
    return logging.LoggerAdapter(logger, {"role": safe})
