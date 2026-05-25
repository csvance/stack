# `stack` CLI

A bash CLI that owns all post-decomposition operations for a stacked-diff
workflow on Azure DevOps. It consumes the `stack-manifest.json` produced by
the `stacked-diff-decomposer` Claude skill and extends it as the stack moves
through its lifecycle.

The decomposer (LLM-driven) runs once per feature to produce the initial
stack and manifest. Everything afterward (rebuild after edits, push to
DevOps, sync onto a moved base, drop a merged bottom PR) is deterministic
and lives here.

## Prerequisites

- bash 4.0 or newer (Linux ships this; on macOS `brew install bash` and make
  sure the brewed bash is on `PATH` before `/bin/bash`)
- `git`
- `jq`
- [`git-branchless`](https://github.com/arxanas/git-branchless), initialized
  in the repo (`git branchless init`)
- Azure CLI (`az`) with the `azure-devops` extension, signed in via
  `az login`
- PyCharm with the JetBrains command-line launcher available as `pycharm`
  on `PATH` (or installed in a standard Linux / macOS location; see
  `install.sh` for the discovery list). PyCharm is used as the three-way
  merge tool for conflict resolution.

## Install

From the repo root:

```
tools/stack/install.sh
```

The installer symlinks `tools/stack/bin/stack` into `~/.local/bin` (override
with `STACK_INSTALL_DIR=...`) and probes each prerequisite, printing a
one-line install hint for anything missing. If `~/.local/bin` is not on your
`PATH`, the installer prints the `export PATH=...` snippet you need to add
to your shell rc.

`tools/stack/uninstall.sh` removes the symlink it created. It refuses to
touch anything else.

## Subcommands

All subcommands accept these global flags:

- `--yes`, `-y` skip confirmation prompts (do not skip choice prompts)
- `--dry-run`, `-n` print what would happen without making changes
- `--verbose`, `-v` verbose logging
- `--structured` machine-readable one-fact-per-line output
- `--manifest <path>` use a manifest at a non-default path

- **`stack status`** Read-only. Prints the manifest summary, reports drift
  between the manifest and local branches (see "Drift detection" below),
  and surfaces any cached PR status. Pass `--verbose` to also render the
  `git branchless smartlog`.
- **`stack abort`** Restores from the most recent backup snapshot.
  `stack abort --list` enumerates available snapshots; `stack abort --set
  <ref>` restores a specific one.
- **`stack update`** Rebuilds the stack after you commit on top of a stack
  branch. Prompts squash vs. additive (or pass `--strategy=squash|additive`),
  then rebases downstream branches via `git branchless move --force-rewrite
  --merge`. Conflicts route to PyCharm. Tree-hash verification gates the
  manifest write when no conflicts occurred; when conflicts did occur, the
  rebuilt result is shown and confirmed before the manifest is written.
- **`stack push`** Force-with-lease every branch and create or update the
  corresponding Azure DevOps PRs. Each PR targets the previous branch in
  the stack (the bottom targets `base_ref`). Title and description come
  from the commit; the body includes a "Part X of Y in stack" header with
  cross-links to every sibling PR (two passes: pass 1 creates with
  placeholder links, pass 2 refreshes descriptions with the actual IDs).
  Handles abandoned and merged PRs interactively.
- **`stack sync`** Fetches origin and rebases the entire stack onto the
  moved base ref via a single `git branchless move`. Verification cherry-
  picks the old commits onto the new base in a throwaway worktree and
  compares the resulting tree to the rebuilt top.
- **`stack land`** Detects whether the bottom PR has been merged (via
  Azure DevOps or `git merge-base --is-ancestor`), drops the bottom from
  the manifest, deletes its local ref, rebases the rest onto the new base,
  and retargets the new bottom's PR. The remaining branches keep their
  original names; the manifest's `order` field is renumbered 1..N-1.

## Drift detection

`stack status` classifies findings; state-changing commands refuse to
proceed when most drift classes are present. Recovery is `stack abort` or
manual reset to manifest state.

| Class | Meaning |
|---|---|
| `MANIFEST_INVALID` | bad JSON, version != 1, or branches out of order |
| `MISSING_BRANCH` | manifest names a branch with no local ref |
| `BRANCH_MOVED` | local SHA differs from recorded `commit_sha` |
| `PARENT_DRIFT` | actual parent commit != recorded parent's SHA |
| `BASE_MOVED` | resolved `base_ref` != recorded `base_branch` |
| `DETACHED_HEAD` | HEAD is not on a branch |
| `DIRTY_WORKTREE` | uncommitted changes or in-progress merge/rebase |
| `UNCOMMITTED_AHEAD` | current branch has commits beyond recorded (input to `stack update`) |
| `PR_MERGED_BOTTOM` | bottom PR is `completed` (input to `stack land`) |
| `PR_STATUS_STALE` | cache more than 24h old |

## Backup snapshots

Every state-changing subcommand creates timestamped backup refs under
`refs/backup/stack/<prefix>/<op>-<UTC-timestamp>/<branch>` before doing
work. They are never auto-deleted; `stack abort` lists and restores them.
Manual cleanup:

```
git for-each-ref refs/backup/stack/<prefix>/
git update-ref -d <ref>
```

## Manifest schema

The base schema is documented in
`.claude/skills/stacked-diff-decomposer/SKILL.md`. This CLI adds the
following backward-compatible fields the first time it touches a manifest:

- optional top-level `base_ref` (e.g. `"origin/master"`) tracked ref for
  `sync` / `land`
- top-level `last_update`
- per-branch `pr_id` / `pr_url`
- top-level `pr_status_cache`
- top-level `verification.current_stack_tip_tree` /
  `verification.last_verified_at`

Manifests written by the decomposer alone (without these fields) remain
valid; the CLI adds them on first use.

## `git branchless` notes

The CLI always passes `--force-rewrite-public-commits` to `git branchless
move`. In a stacked-diff workflow we routinely rewrite previously-pushed
branches, which branchless treats as "public" by default. The flag opts in
to the rewrite explicitly.

## Testing

Bats tests live under `tools/stack/tests/bats/`. Each test builds its own
fixture repo via helpers in `fixtures.bash`. PyCharm and `az` are mocked
by scripts at the front of `PATH`. Run with:

```
cd tools/stack
bats tests/bats
```

## Layout

```
tools/stack/
  bin/stack            dispatcher
  libexec/             one script per subcommand
  lib/                 shared bash libraries (sourced)
  share/               static assets (PR description template)
  tests/bats/          bats tests + fixtures + mocks
  install.sh           idempotent installer (~/.local/bin symlink)
  uninstall.sh         remove the installer's symlink
```
