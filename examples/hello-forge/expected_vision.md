# Expected Vision: hello-forge

*Hand-written. This is what the Skald role should extract from
`goal.md` — the `Vision` dataclass fields (goal, priorities,
non-goals, success criteria, ambiguities). Used as a test fixture:
a Skald implementation passes when its interpretation matches this
document in substance.*

## Goal

Build a tiny CLI that greets by name and remembers the last greeting
across runs.

## Priorities

1. The greeting must include the person's name.
2. The last greeted name must persist between runs (a small file in
   the user's home directory is acceptable).
3. Single Python script; standard library only — no dependencies.
4. Small and readable: this is a learning exercise.

## Non-goals

- No database.
- No web server.
- No configuration system.

## Success criteria

- Running the CLI with a name prints a greeting containing that name.
- Running it a second time with a different name recalls the previous
  name (verifiable by hand).
- The implementation is one script with no third-party imports.

## Ambiguities

- Language support: should the greeting be English-only, or support
  other languages from the start? (The goal text asks this outright —
  the Skald must surface it, not decide it.)
