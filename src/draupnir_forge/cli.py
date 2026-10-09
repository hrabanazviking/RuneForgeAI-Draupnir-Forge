"""cli.py — command-line interface for Draupnir Forge.

Slice 1 provides the skeleton: --version and subcommand help scaffolding.
Slice 4 (CLI skeleton, full) expands this into init/forge/status/roadmap/
events/checkpoint with proper dispatch. The skeleton is shaped so slice 4
only fills in handlers, never restructures.
"""

from __future__ import annotations

import argparse
import sys

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
    for name in SUBCOMMANDS:
        sub.add_parser(name, help=f"(slice 4) {name} command.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stdout)
        return 0
    # Slice 4 wires real handlers; until then, report honestly.
    print(f"draupnir {args.command}: not yet implemented (see roadmap slice 4).")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
