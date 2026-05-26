# `stack` CLI

A bash CLI that owns all post-decomposition operations for a stacked-diff
workflow on Azure DevOps. It consumes the `stack-manifest.json` produced by
the `stacked-diff-decomposer` Claude skill and extends it as the stack moves
through its lifecycle.

The decomposer (LLM-driven) runs once per feature to produce the initial
stack and manifest. Everything afterward (rebuild after edits, push to
DevOps, sync onto a moved base, drop a merged bottom PR) is deterministic
and lives here.

Target environment: Linux, with `~/.local/bin` on every user's `PATH`.

## Prerequisites

- `bash` 4 or newer (ships with every supported distribution)
- `git`
- `jq`
- [`git-branchless`](https://github.com/arxanas/git-branchless), initialized
  in the repo (`git branchless init`)
- Azure CLI (`az`) with the `azure-devops` extension, signed in via
  `az login` (Azure DevOps cloud) or `az devops login --organization
  <org-url>` with a PAT (Azure DevOps Server / on-prem). The PAT needs
  one scope: **Code (read & write)**. This single scope covers everything
  the CLI does:

  | Operation                                | API surface           |
  | ---------------------------------------- | --------------------- |
  | `git push` over HTTPS                    | Code (write)          |
  | `az repos show` (preflight reachability) | Code (read)           |
  | `az repos pr list` / `show`              | Code (read)           |
  | `az repos pr create` / `update`          | Code (write)          |

  No other scopes are required: the CLI does not post PR comments, set
  commit statuses, or read work items. You do not need to set a default
  organization (`az devops configure --defaults organization=...`); the
  CLI parses the origin remote and passes `--organization` explicitly on
  every call.
- PyCharm with a `pycharm` shell script on `PATH`. See "JetBrains Toolbox"
  below for the recommended setup. PyCharm is used as the three-way merge
  tool for conflict resolution.

## Install

From the repo root:

```
tools/stack/install.sh
```

The installer symlinks `tools/stack/bin/stack` into `~/.local/bin` (override
with `STACK_INSTALL_DIR=...`) and probes each prerequisite, printing a
one-line install hint for anything missing. Because `~/.local/bin` is on
every user's `PATH` at this company, the `stack` command is available in a
new shell immediately after install.

`tools/stack/uninstall.sh` removes the symlink it created. It refuses to
touch anything else.

## JetBrains Toolbox: shell scripts in `~/.local/bin`

PyCharm ships a small command-line launcher script that the `stack` CLI
invokes as `pycharm` for three-way merges. The cleanest way to install and
maintain this launcher on Linux is through **JetBrains Toolbox**, which can
generate the script in `~/.local/bin` and keep it in sync with the
installed IDE version as you upgrade.

One-time setup (per user):

1. Open **Toolbox** and click the gear icon at the top right, then
   **Settings**.
2. Under **Tools**, enable **Generate shell scripts**.
3. Set **Shell scripts location** to `~/.local/bin`.
4. Leave **Shell script name** at its default (lowercase IDE name, so
   `pycharm` for PyCharm Professional/Community).
5. In the IDE's individual settings inside Toolbox, make sure
   **Shell script** is enabled.

Verify:

```
command -v pycharm
pycharm --version
```

Because `~/.local/bin` is already on every user's `PATH`, no shell-rc
changes are needed. Toolbox refreshes the script whenever PyCharm updates,
so the launcher stays current without manual intervention. The same
mechanism works for any other JetBrains IDE if you ever switch the merge
tool (IntelliJ, GoLand, WebStorm).

## Subcommands

All subcommands accept these global flags:

- `--yes`, `-y` skip confirmation prompts (do not skip choice prompts)
- `--dry-run`, `-n` print what would happen without making changes
- `--verbose`, `-v` verbose logging
- `--stack <prefix>` operate on the stack with this `stack_prefix` (default:
  pick the stack whose branches contain the currently checked-out branch)

Each repository can hold multiple stacks at once. Manifests live under
`.git/stack/manifests/<stack_prefix>.json`. A legacy `stack-manifest.json`
at the repo root is migrated to the new location on first invocation.

- **`stack list`** Read-only. Enumerate all stacks in the repo, one row per
  `stack_prefix` with branch count and `base_ref`. A `*` marker indicates
  the stack containing the current branch.
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
  the stack (the bottom targets `base_ref`). The root PR (the original
  bottom of the stack, recorded once on first push and preserved across
  `stack land`) carries the canonical stack index in its description.
  Every non-root PR carries a single back-link to the root PR. Titles are
  stable as `[Part N] <commit subject>`; appending or landing branches
  does not require rewriting other titles. Handles abandoned and merged
  PRs interactively.
- **`stack sync`** Fetches origin and rebases the entire stack onto the
  moved base ref via a single `git branchless move`. Verification cherry-
  picks the old commits onto the new base in a throwaway worktree and
  compares the resulting tree to the rebuilt top.
- **`stack land`** Detects whether the bottom PR has been merged (via
  Azure DevOps or `git merge-base --is-ancestor`), drops the bottom from
  the manifest, deletes its local ref, rebases the rest onto the new base,
  and retargets the new bottom's PR. The remaining branches keep both
  their names and their original `order` values; gaps in the order sequence
  after a land are intentional and a stable identifier matching the branch
  name suffix. When the merged bottom was the only branch in the stack,
  the manifest file is removed entirely.

## Drift detection

`stack status` classifies findings; state-changing commands refuse to
proceed when most drift classes are present. Recovery is `stack abort` or
manual reset to manifest state.

Preconditions (reported separately from drift; block state-changing
subcommands):

- detached HEAD (HEAD is not on a branch)
- dirty working tree (uncommitted changes or in-progress merge/rebase)
- invalid manifest (bad JSON, missing required fields)

Drift classes:

| Class               | Meaning                                                                |
| ------------------- | ---------------------------------------------------------------------- |
| `MISSING_BRANCH`    | manifest names a branch with no local ref                              |
| `BRANCH_MOVED`      | local SHA differs from recorded `commit_sha`                           |
| `PARENT_DRIFT`      | actual parent commit != recorded parent's SHA                          |
| `BASE_MOVED`        | resolved `base_ref` != recorded `base_branch`                          |
| `UNCOMMITTED_AHEAD` | current branch has commits beyond recorded (input to `stack update`)   |
| `PR_MERGED_BOTTOM`  | bottom PR is `completed` (input to `stack land`)                       |

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
- top-level `root_pr_id` / `root_pr_url` (set once on first `stack push`;
  preserved across `stack land`)
- per-branch `pr_id` / `pr_url`
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
