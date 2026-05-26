# stack

CLI for working with stacked diffs against Azure DevOps. A developer with a feature branch decomposes it into a sequence of small, logically separated branches, each reviewed as a separate pull request that targets the one below it.

## Status

The create-through-publish workflow is implemented (`stack create` chains four idempotent phases). The post-creation operations (`stack push` for re-push, `land`, `update`, `sync`, `abort`, `list`) and StackBot (the webhook service that automates lifecycle once a stack exists) are tracked in the project plan and not yet shipped.

## Install

Requires Python 3.12 or 3.13, `git`, `git-branchless`, and access to a Redis instance.

```sh
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

This registers a `stack` console script.

## Configure

Create `~/.stack/config.yaml`:

```yaml
redis:
  host: localhost
  port: 6379
  key_prefix: stack
ado:
  organization_url: https://dev.azure.com/your-org
  pat: ${oc.env:STACK_ADO_PAT}
branch_suffix: "-stacked-"
```

Set your ADO Personal Access Token in the `STACK_ADO_PAT` environment variable (the config interpolates it via OmegaConf). The PAT needs the "Code (read & write)" scope.

Override the config path with `STACK_CONFIG=/path/to/config.yaml`.

## Usage

From inside a git repo whose `origin` remote is an ADO repository:

```sh
# Single command, walks through prepare → decompose → manifest → publish
stack create --prefix my-feature --base origin/main

# Or run each phase individually (each is idempotent)
stack prepare  --prefix my-feature --base origin/main
stack decompose --prefix my-feature
stack manifest --prefix my-feature
stack publish  --prefix my-feature

# Inspect an existing stack
stack status --stack my-feature
```

`stack decompose` launches Claude Code in the repo, which runs the `stacked-diff-decomposer` skill to split your feature branch into a chain of `<prefix>-stacked-<n>` branches. The skill writes a sentinel file; `stack manifest` reads it and constructs the manifest in Redis.

## Development

```sh
pytest tests/                       # full suite (fakeredis backend by default)
pytest tests/ --redis-url redis://localhost:6379  # run state-store tests against real Redis
mypy --strict stack_core stack_cli
ruff check .
```

## Layout

- `stack_core/` — pure library: types, manifest helpers, topology, Redis state store, git wrappers, ADO client, drift detection, verify, PR templates, per-operation business logic.
- `stack_cli/` — Typer-based command line. Imports from `stack_core` and renders results.
- `stack_bot/` — async FastAPI shell that receives Azure DevOps webhooks and drives `stack_core` operations as background tasks.
- `tests/unit/` — unit tests with mocked redis (fakeredis) and ADO (respx). Fast.
- `tests/integration/` — exercises multiple modules together. Slower.
- `.claude/skills/stacked-diff-decomposer/` — Claude Code skill invoked by `stack decompose`.

## Concurrency model

`stack_core` and `stack_cli` are pure sync. The CLI calls into core directly, and Redis, ADO HTTP, and git subprocess work are all blocking by design.

`stack_bot` is the only async surface. Route handlers and background tasks are `async def`, and they bridge to sync core code via `asyncio.to_thread` at the I/O boundary (see `stack_bot/webhooks/ado.py`, `stack_bot/handlers/land.py`, `stack_bot/notifications.py`, `stack_bot/workspace_mgr.py`). Core modules are never forked into async variants.

New bot code that does I/O should follow the same rule: wrap the sync core call at the boundary with `asyncio.to_thread`, and do not introduce parallel async versions of `stack_core` modules. This keeps the "color of functions" boundary at one well-defined seam.
