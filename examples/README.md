# Draupnir Forge — Examples

Worked examples: a goal on the way in, the artifacts the Forge should
produce on the way out. They double as fixtures for tests and docs.

## hello-forge

The smallest meaningful forge run: *"Build a tiny CLI that greets by
name and remembers the last greeting."*

| File | What it is |
|---|---|
| `hello-forge/goal.md` | The human's raw intent, exactly as spoken — the input to `draupnir forge` and to the Skald |
| `hello-forge/expected_vision.md` | The vision the Skald should extract: goal, priorities, non-goals, success criteria, ambiguities (hand-written; used as a test fixture) |

### Running the forge on it

The autonomous loop lands in slice 46. Until then, you can walk the
states the loop *would* take:

```bash
# from the repo root
draupnir init /tmp/hello-forge-run
draupnir --project-dir /tmp/hello-forge-run forge --dry-run
```

After slice 46, the real run looks like this:

```bash
draupnir init /tmp/hello-forge-run
cp examples/hello-forge/goal.md /tmp/hello-forge-run/goal.md
draupnir --project-dir /tmp/hello-forge-run forge
draupnir --project-dir /tmp/hello-forge-run status
```

Expected arc: Skald reads `goal.md` → `VISION_UPDATED` (compare with
`expected_vision.md`) → Cartographer/Architect/Planner produce the
`.mythis/` docs and a one- or two-task roadmap → the ForgeWorker
implements the script → Tester runs it twice with different names →
Verifier checks the success criteria → `PROJECT_COMPLETED`.

### Using the fixture in tests

```python
from pathlib import Path
from draupnir_forge.roles.skald import interpret

goal = Path("examples/hello-forge/goal.md").read_text()
vision = interpret(goal)
assert "greet" in vision.goal.lower()
assert vision.non_goals  # database, web server, config system refused
assert vision.ambiguities  # the language question is surfaced, not decided
```
