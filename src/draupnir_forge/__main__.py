"""Entry point: `python -m draupnir_forge` behaves like the `draupnir` CLI."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
