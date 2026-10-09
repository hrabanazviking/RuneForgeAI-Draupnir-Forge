"""cli.py — command-line interface for Draupnir Forge.

Slice 1 provided the skeleton (--version and subcommand help scaffolding).
Slice 4 fills in real handlers for every subcommand while keeping the
``build_parser()`` / ``SUBCOMMANDS`` / ``main()`` structure intact.

Design notes (for future slices):
  - All user-facing output goes through ``print()`` calls in *this module
    only*. Library code must never print; it returns values or raises.
  - ``forge`` (slice 20), ``checkpoint`` (slice 28), ``metrics`` (slice 41)
    and the full ``init`` modes (slices 29/30) are honestly reported as
    not-yet-implemented with exit code 2 rather than faked.
  - Every read-only command tolerates a missing ``.mythis/`` tree ("not
    found" message, exit 0); corrupt JSON is reported on stderr, exit 2.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__

# Subcommands reserved by the roadmap. Slice 4 implements their handlers.
# Declared here so `draupnir <cmd> --help` already resolves.
SUBCOMMANDS = (
    "init",        # slice 4 (+29/30): create .mythis/ project skeleton
    "forge",       # slice 4 (+20): run the autonomous loop
    "status",      # slice 4 (+31): black-box progress view
    "roadmap",     # slice 4 (+32): show task graph
    "events",      # slice 4 (+32): inspect event log
    "checkpoint",  # slice 4 (+28): git checkpoint now
    "metrics",     # slice 41: success metrics dashboard
)

# The forge's project memory root and the skeleton it must always carry.
_MYTHIS_DIR = ".mythis"
_SKELETON_DIRS = ("logs", "evidence", "sessions")
_PROJECT_STATE_FILE = "PROJECT_STATE.json"
_ROADMAP_FILE = "roadmap.json"
_EVENTS_FILE = "events.jsonl"

_DEFAULT_EVENT_LIMIT = 20


def _configure_logging(verbose: bool, quiet: bool) -> None:
    """Wire --verbose/--quiet to the logging subsystem.

    Slice 3 provides ``draupnir_forge.log``; until it lands we fall back
    to a plain ``basicConfig`` so the CLI never crashes on import.
    """
    try:
        from .log import configure_logging  # noqa: F401  (slice 3, pending)
    except ImportError:
        configure_logging = None  # type: ignore[assignment]
    if configure_logging is not None:
        configure_logging(verbose=verbose, quiet=quiet)
    else:
        if verbose:
            level = logging.DEBUG
        elif quiet:
            level = logging.WARNING
        else:
            level = logging.INFO
        logging.basicConfig(level=level)


def _project_root(args: argparse.Namespace) -> Path:
    """Resolve the target project directory from --project-dir."""
    return Path(str(args.project_dir)).expanduser()


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Handlers — one per subcommand. Each returns a process exit code.
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    """Create the .mythis/ skeleton: logs/, evidence/, sessions/ + state.

    Idempotent: existing directories and an existing PROJECT_STATE.json are
    left untouched (additive only). Prints the created path. Exit 0.
    """
    target = Path(str(args.path)).expanduser() if args.path else _project_root(args)
    mythis = target / _MYTHIS_DIR
    try:
        for subdir in _SKELETON_DIRS:
            (mythis / subdir).mkdir(parents=True, exist_ok=True)
        state_path = mythis / _PROJECT_STATE_FILE
        if not state_path.exists():
            state = {
                "phase": "INTAKE",
                "goal": "",
                "updated": _utc_now_iso(),
            }
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"draupnir init: cannot create project skeleton: {exc}", file=sys.stderr)
        return 2
    print(f"Created Draupnir Forge project skeleton at {target.resolve()}")
    return 0


def cmd_forge(args: argparse.Namespace) -> int:
    """Run the autonomous forge loop. Lands in slice 20 — report honestly."""
    del args  # the loop does not exist yet; nothing to consume
    print("forge loop not yet implemented (slice 20)")
    return 2


def cmd_status(args: argparse.Namespace) -> int:
    """Show phase/goal/updated from .mythis/PROJECT_STATE.json. Exit 0/2."""
    state_path = _project_root(args) / _MYTHIS_DIR / _PROJECT_STATE_FILE
    if not state_path.is_file():
        print("no project: run 'draupnir init' to create a project skeleton")
        return 0
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"draupnir status: cannot read project state: {exc}", file=sys.stderr)
        return 2
    if not isinstance(state, dict):
        print("draupnir status: project state is not a JSON object", file=sys.stderr)
        return 2
    print(f"phase:   {state.get('phase', 'UNKNOWN')}")
    goal = state.get("goal") or "(none)"
    print(f"goal:    {goal}")
    print(f"updated: {state.get('updated', '(unknown)')}")
    return 0


def _extract_tasks(data: Any) -> list[Any]:
    """Pull a task list out of a roadmap document of unknown shape."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("tasks", "slices", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def cmd_roadmap(args: argparse.Namespace) -> int:
    """Print the task table from .mythis/roadmap.json. Exit 0/2."""
    roadmap_path = _project_root(args) / _MYTHIS_DIR / _ROADMAP_FILE
    if not roadmap_path.is_file():
        print("no roadmap yet")
        return 0
    try:
        data = json.loads(roadmap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"draupnir roadmap: cannot read roadmap: {exc}", file=sys.stderr)
        return 2
    tasks = _extract_tasks(data)
    if not tasks:
        print("roadmap is empty")
        return 0
    print(f"{'ID':<10}{'STATUS':<14}TITLE")
    for task in tasks:
        if isinstance(task, dict):
            task_id = str(task.get("id", task.get("slice", "?")))
            status = str(task.get("status", task.get("state", "?")))
            title = str(task.get("title", task.get("name", "")))
        else:
            task_id, status, title = "?", "?", str(task)
        print(f"{task_id:<10}{status:<14}{title}")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    """Tail .mythis/events.jsonl (last 20, --limit N, --type T). Exit 0/2."""
    events_path = _project_root(args) / _MYTHIS_DIR / _EVENTS_FILE
    if not events_path.is_file():
        print("no events")
        return 0
    limit = args.limit if isinstance(args.limit, int) else _DEFAULT_EVENT_LIMIT
    type_filter = str(args.type) if args.type else None
    rows: list[dict[str, Any]] = []
    skipped = 0
    try:
        with events_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1  # self-healing: skip corrupt lines, keep reading
                    continue
                if not isinstance(event, dict):
                    skipped += 1
                    continue
                if type_filter is not None and str(
                    event.get("type", "")
                ) != type_filter:
                    continue
                rows.append(event)
    except OSError as exc:
        print(f"draupnir events: cannot read event log: {exc}", file=sys.stderr)
        return 2
    tail = rows[-limit:] if limit > 0 else []
    for event in tail:
        seq = event.get("seq", "-")
        ts = event.get("ts", "?")
        event_type = event.get("type", "?")
        actor = event.get("actor_role", "-")
        print(f"{seq!s:>6}  {ts}  {event_type}  {actor}")
    if skipped:
        print(f"({skipped} unreadable event line(s) skipped)", file=sys.stderr)
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    """Take a git checkpoint now. Lands in slice 28 — report honestly."""
    del args
    print("checkpoints land in slice 28")
    return 2


def cmd_metrics(args: argparse.Namespace) -> int:
    """Show the success-metrics dashboard. Lands in slice 41."""
    del args
    print("metrics land in slice 41")
    return 2


_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "init": cmd_init,
    "forge": cmd_forge,
    "status": cmd_status,
    "roadmap": cmd_roadmap,
    "events": cmd_events,
    "checkpoint": cmd_checkpoint,
    "metrics": cmd_metrics,
}


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="draupnir",
        description=(
            "Draupnir Forge — one intent, a thousand hammer blows, "
            "one coherent system."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Verbose logging."
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Only warnings and errors."
    )
    parser.add_argument(
        "--project-dir", default=".",
        help="Project directory (default: current directory).",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    parsers: dict[str, argparse.ArgumentParser] = {}
    for name in SUBCOMMANDS:
        parsers[name] = sub.add_parser(name, help=f"(slice 4) {name} command.")
    # Per-subcommand arguments (kept here so the SUBCOMMANDS loop above
    # never needs restructuring when future slices add more options).
    parsers["init"].add_argument(
        "path", nargs="?", default=None,
        help="Directory to initialize (default: --project-dir).",
    )
    parsers["events"].add_argument(
        "--limit", type=int, default=_DEFAULT_EVENT_LIMIT,
        help=f"Show the last N events (default: {_DEFAULT_EVENT_LIMIT}).",
    )
    parsers["events"].add_argument(
        "--type", default=None,
        help="Only show events of this type.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(bool(args.verbose), bool(args.quiet))
    if args.command is None:
        parser.print_help(sys.stdout)
        return 0
    handler = _HANDLERS.get(args.command)
    if handler is None:
        # Unreachable via argparse (invalid choices exit 2 there), but kept
        # as a guard so dispatch can never silently fall through.
        print(f"draupnir {args.command}: not yet implemented.", file=sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
