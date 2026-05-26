# pystack

Python core library for the stacked-diff workflow tooling. Phase 1 deliverable: the foundation that the CLI rewrite and StackBot will both consume.

## Scope

This package contains the shared core only. No CLI, no webhook server, no bot. It exposes:

- `stack_core.types`: Pydantic models for manifests and audit entries.
- `stack_core.exceptions`: structured exception hierarchy.
- `stack_core.manifest`: pure helpers for manifest construction and transformation.
- `stack_core.topology`: pure functions over manifest data.
- `stack_core.git_ops`: subprocess wrappers around `git` and `git-branchless`.
- `stack_core.state_store`: Redis-backed transactional manifest CRUD.

## Development

```sh
pip install -e ".[dev]"
pytest tests/unit              # fast, no external deps
pytest tests/integration       # uses fakeredis by default
pytest --redis-url redis://localhost:6379  # run integration against a real Redis
mypy --strict stack_core
ruff check .
```
