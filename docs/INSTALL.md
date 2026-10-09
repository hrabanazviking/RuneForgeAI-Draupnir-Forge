# Installing Draupnir Forge

## Quickstart

Requirements: **Python 3.10+** and `git`. The only runtime dependency
is `pyyaml` (installed automatically).

```bash
# 1. Clone
git clone <repo-url> draupnir-forge
cd draupnir-forge

# 2. Use a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install (editable, so repo edits take effect immediately)
pip install -e .

# 4. Verify
draupnir --version               # → draupnir 0.1.0

# 5. Forge something
draupnir init myproj
draupnir --project-dir myproj status
```

A plain (non-editable) `pip install .` also works: the YAML data files
under `src/draupnir_forge/data/` are declared as package data, so the
installed package finds its own defaults without the repo checkout.

## What `draupnir init` does

Creates `<dir>/.mythis/` — the project's memory root: `PROJECT_STATE.json`
(phase tracker), `logs/`, `evidence/`, `sessions/`. Idempotent: existing
files are left untouched.

## Configuration

On first model use you need a provider. Defaults assume OpenAI:

```bash
export OPENAI_API_KEY=sk-...
```

Or point the Forge at a local model (no key needed):

```yaml
# ~/.draupnir/config.yaml  (or <project>/.mythis/config.yaml)
model:
  provider: ollama
  fallback_providers: [openai]   # tried after repeated 5xx on ollama
```

Config layers, weakest to strongest: built-in defaults <
`~/.draupnir/config.yaml` < `.mythis/config.yaml` < `DRAUPNIR_*`
environment variables (`__` nests, e.g. `DRAUPNIR_MODEL__PROVIDER=ollama`)
< explicit overrides.

## Troubleshooting

**`pip install` refuses with "externally managed environment" (PEP 668).**
Your system Python is protected — this is normal on Debian/Ubuntu 23.04+,
Fedora, and similar. Use a venv (step 2 above); never `pip install`
with `--break-system-packages`.

**`draupnir: command not found` after install.**
The install prefix's `bin/` is not on your `PATH`. Either activate the
venv (`source .venv/bin/activate`) or run it explicitly:
`.venv/bin/draupnir --version`.

**Tests fail with `ModuleNotFoundError: No module named 'draupnir_forge'`.**
The test suite runs against the source tree; it needs the package
importable. Either install first (`pip install -e .`) or run with the
source on the path:

```bash
PYTHONPATH=src python3 -m unittest discover tests
```

**The Forge can't find its data files** (`Built-in data file … not found`).
The resolver searches, in order: the `DRAUPNIR_DATA_DIR` environment
variable → the installed package's data → a `data/` directory found by
walking up from the source tree → `./data`. If you moved things around,
point the override at the directory holding the YAML files:

```bash
export DRAUPNIR_DATA_DIR=/path/to/draupnir-forge/data
```

**Model calls fail with `ModelError: … API key … is not set`.**
The `openai` provider needs the env var named by `model.api_key_env`
(default `OPENAI_API_KEY`). Export it, or switch providers
(`model.provider: ollama`, keyless on `http://localhost:11434`).
