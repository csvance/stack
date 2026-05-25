---
name: stacked-diff-decomposer
description: Decompose a completed git feature branch into a stack of dependent branches (feature-branch-1, feature-branch-2, etc.) where each branch adds exactly one logical commit on top of the previous one, suitable for review as a series of small stacked pull requests. Use this skill whenever the user wants to break down a large feature branch into smaller reviewable pieces, prepare stacked diffs or stacked PRs, split a big change into a review-friendly sequence, or restructure a feature branch before opening pull requests. Trigger on phrases like "decompose this branch", "break this into a stacked diff", "split this PR", "make this reviewable", "stack this up", or when the user references preparing a feature branch for review. Also produces a stack manifest JSON file describing the stack for downstream Azure DevOps tooling.
---

# Stacked Diff Decomposer

This skill takes a completed feature branch and rewrites it as a stack of dependent branches, each adding one logical commit on top of the previous. The final branch in the stack must produce byte-identical contents to the original feature branch tip. This correctness check is non-negotiable: if it fails, the stack is rejected.

The skill builds on git-branchless for cleaner state management, visualization, and recovery. git-branchless is a required dependency.

## When to use this skill

Trigger when the user has a feature branch ready for review and wants it broken into a series of small, reviewable pieces. Typical phrasings: "decompose this branch", "split this into a stacked diff", "make this reviewable", "break this PR up". Also trigger when the user references preparing branches for stacked-PR tooling.

Do NOT trigger for:
- Work-in-progress branches the user is still actively editing
- Cleanup of a branch that's already a clean stack of meaningful commits (it's already done)
- Single-commit branches (nothing to decompose)
- General git history rewriting unrelated to review preparation
- Mechanical mass-rename, mass-format, or symbol-rename branches where every file changes for the same reason. Intermediate commits in a split rename are not internally consistent (a commit that renames the source module but not its callers, or vice versa, leaves the tree broken at HEAD). The skill can still produce a verified stack, but the resulting PRs are not independently reviewable in the way the skill is designed for. If the user insists on running it anyway (e.g., to exercise the tooling), flag this caveat up front before proposing the decomposition.

## Inputs you need before starting

Before any git operations, confirm these with the user:

1. **The feature branch name.** The branch to be decomposed.
2. **The base branch.** Usually `main` or `master`, but ask if unclear. The stack will be built on top of this.
3. **The stack name prefix.** Defaults to the feature branch name itself. If the feature branch is `add-user-auth`, the stack branches will be `add-user-auth-1`, `add-user-auth-2`, etc.
4. **Confirmation that the working tree is clean.** Run `git status` and confirm with the user before proceeding. Refuse to continue if there are uncommitted changes.

## Workflow overview

The skill proceeds in six phases. Do not skip phases or reorder them.

1. **Preflight**: Verify git-branchless is installed and initialized in this repo.
2. **Safety setup**: Verify clean state, record the original tip commit, create a backup ref.
3. **Analysis**: Inspect the diff between base and feature branch, propose a decomposition.
4. **User approval**: Present the proposed decomposition, get explicit approval before modifying anything.
5. **Stack construction**: Create the stack of branches, one commit per branch.
6. **Correctness verification**: Compare the final branch tip to the original. If different, abort and restore.

## Phase 1: Preflight

Verify git-branchless is available and initialized. If either check fails, stop and give the user installation instructions rather than falling back to raw git.

```bash
# Check that git-branchless is installed
git branchless --version
# If this fails, tell the user to install git-branchless and stop.
# Installation: https://github.com/arxanas/git-branchless#installation

# Check that git-branchless is initialized in this repo (hooks installed, event log present)
git branchless query "all()" >/dev/null 2>&1
# If this fails, tell the user to run: git branchless init
# Then stop and let them run it.
```

Do not proceed past preflight if either check fails. The skill's recovery semantics depend on git-branchless being available.

## Phase 2: Safety setup

Run these checks in order. Stop and report to the user on any failure.

```bash
# Confirm we're in a git repo
git rev-parse --git-dir

# Confirm working tree is clean
git status --porcelain
# If output is non-empty, stop and tell the user to commit or stash first.

# Record the original feature branch tip - this is the source of truth for correctness
git rev-parse <feature-branch>
# Save this commit hash. The final stack branch must produce the same tree as this commit.

# Create a backup ref so the user can recover if anything goes wrong
git update-ref refs/backup/<feature-branch>-<timestamp> <feature-branch>
```

Tell the user the backup ref name explicitly. They can recover via `git undo` for recent operations or via `git reset --hard refs/backup/<name>` for full recovery from the backup ref.

## Phase 3: Analysis

Get the full diff to analyze:

```bash
# List files changed
git diff --name-status <base-branch>..<feature-branch>

# Get the full diff for analysis
git diff <base-branch>..<feature-branch>

# Also useful: stat summary
git diff --stat <base-branch>..<feature-branch>
```

Analyze the diff for natural decomposition boundaries. Look for these patterns in order of preference:

1. **Layered architectural changes**: data model, then API layer, then UI layer, then tests. Each layer typically depends on the previous.
2. **Feature flag or scaffolding first**: configuration, feature flags, type definitions, or interfaces that later commits implement.
3. **Independent supporting changes**: refactors, utility additions, or unrelated fixes that can ship first as prerequisites.
4. **Test additions alongside their code**: prefer keeping tests in the same commit as the code they test, not as a separate "add tests" commit at the end.
5. **Schema or migration changes**: these almost always want their own commit early in the stack.

Avoid these anti-patterns when proposing splits:
- Splitting purely by file or directory without regard to logical coherence
- Creating a commit that doesn't compile or pass basic syntax checks on its own
- Putting all tests in a final commit (reviewers can't tell if the earlier commits are correct)
- Creating more than ~6 commits unless the change genuinely justifies it; aim for 2-5

For each proposed commit in the stack, decide:
- Which files (or hunks within files) belong to it
- A short commit message subject (50 chars or less)
- A 1-2 sentence body explaining what this piece does and why it's grouped this way

## Phase 4: User approval

Present the proposed decomposition to the user as a numbered list, showing for each commit:
- The proposed commit message
- The files involved
- A brief rationale for the grouping

Ask the user to approve, request changes, or abort. Do not proceed without explicit approval. If the user requests changes, revise and re-present.

If the user wants to see the actual hunks for a proposed commit before approving, show them with `git diff <base> -- <files>` filtered appropriately.

## Phase 5: Stack construction

Once approved, build the stack. The approach uses `git checkout` plus targeted file/hunk staging from the feature branch.

For a stack of N commits with prefix `<prefix>`:

```bash
# Start from the base branch
git checkout <base-branch>
git checkout -b <prefix>-1

# For each commit i from 1 to N:
#   Apply the changes for commit i from the feature branch
#   Commit them with the approved message
#   If i < N, create the next branch on top
```

The applying step depends on whether the commit splits cleanly along file boundaries or needs hunk-level splitting.

### Whole-file commits

When a commit consists of entire files from the feature branch:

```bash
# Get the files at their feature-branch state
git checkout <feature-branch> -- <file1> <file2> ...
git add <file1> <file2> ...
git commit -m "<approved message>"
```

**Renamed files:** when `git diff --name-status <base>..<feature>` shows an `R` entry (e.g. `R094  old/path.jl  new/path.jl`), check out the new path only. Git's index will record the operation as a rename automatically:

```bash
git checkout <feature-branch> -- <new/path>
# Do NOT also `git rm <old/path>` — that path no longer exists on disk
# at the base, and the checkout above already updates the index to drop it.
git status   # should show: R  old/path -> new/path
```

### Hunk-level commits

When a commit needs only part of a file's changes:

```bash
# Apply the full file change first
git checkout <feature-branch> -- <file>

# Then interactively unstage hunks that belong to later commits
git reset HEAD <file>
git add -p <file>
# Use 'y' for hunks belonging to this commit, 'n' for later ones
# After staging, the unstaged remainder will be picked up by later commits

# Stash the unstaged changes so they don't interfere
git stash push --keep-index -m "stack-construction-temp"
git commit -m "<approved message>"
git stash pop
```

This is more error-prone, so when proposing commits in Phase 2, prefer splits that fall along file boundaries when possible. Use hunk-level only when the logical structure genuinely demands it.

### Creating the next branch in the stack

After each commit (except the last):

```bash
git checkout -b <prefix>-<i+1>
```

The new branch sits on top of the previous one, so the next commit will stack naturally.

## Phase 6: Correctness verification

This is the critical step. After the stack is built, verify that the tip of the last stack branch produces an identical tree to the original feature branch.

```bash
# Compare trees, not commit hashes (commit hashes will differ due to different parents/messages)
ORIGINAL_TREE=$(git rev-parse <feature-branch>^{tree})
STACK_TIP_TREE=$(git rev-parse <prefix>-<N>^{tree})

if [ "$ORIGINAL_TREE" = "$STACK_TIP_TREE" ]; then
  echo "VERIFIED: stack tip matches original feature branch"
else
  echo "MISMATCH: stack does not reproduce original feature branch"
  # Show what differs
  git diff <prefix>-<N>..<feature-branch>
fi
```

If the trees match, proceed to manifest generation.

If the trees do NOT match, this is a hard failure. Do these steps:
1. Report the mismatch to the user explicitly, showing the diff between the stack tip and original
2. Hide the partial stack branches with `git hide <prefix>-1 <prefix>-2 ...` (commits remain recoverable but disappear from the smartlog)
3. Delete the stack branch refs: `git branch -D <prefix>-1 <prefix>-2 ...`
4. Tell the user the original feature branch is unchanged and the backup ref is still in place
5. Mention that `git undo` can also walk back the operations if they prefer that path
6. Do not attempt automatic recovery or "fix-up" commits. The decomposition failed; the user needs to know.

## Manifest generation

After successful verification, write a manifest file at `.git/stack/manifests/<stack_prefix>.json` describing the stack. This file is consumed by the `stack` CLI for all post-decomposition operations. The path is per-stack so multiple stacks can coexist in the same repository; the `<stack_prefix>` portion matches the prefix used when naming branches.

Format:

```json
{
  "version": 1,
  "created_at": "<ISO 8601 timestamp>",
  "base_branch": "<base-branch>",
  "base_ref": "<base-branch>",
  "original_feature_branch": "<feature-branch>",
  "original_tip_commit": "<full sha>",
  "stack_prefix": "<prefix>",
  "branches": [
    {
      "order": 1,
      "name": "<prefix>-1",
      "commit_sha": "<full sha>",
      "commit_subject": "<subject line>",
      "commit_body": "<body or empty string>",
      "parent_branch": "<base-branch>",
      "files_changed": ["path/to/file1", "path/to/file2"]
    },
    {
      "order": 2,
      "name": "<prefix>-2",
      "commit_sha": "<full sha>",
      "commit_subject": "<subject line>",
      "commit_body": "<body or empty string>",
      "parent_branch": "<prefix>-1",
      "files_changed": ["path/to/file3"]
    }
  ],
  "verification": {
    "passed": true,
    "method": "tree-hash-equality",
    "original_tree": "<tree sha>",
    "stack_tip_tree": "<tree sha>"
  }
}
```

The manifest is the contract with downstream tooling. Do not write it unless verification passed. The presence of this file with `verification.passed: true` is what the Azure DevOps tooling will check before pushing branches and creating PRs.

The `stack` CLI may add the following fields on first use; the decomposer omits them:

- `root_pr_id` / `root_pr_url` (top-level): the canonical root PR for the stack, set once on first `stack push` and preserved across `stack land`.
- `pr_id` / `pr_url` (per-branch): the PR for each branch, set on `stack push`.
- `last_update` (top-level): records the most recent `stack update`.
- `verification.current_stack_tip_tree` / `verification.last_verified_at`: refreshed on each `stack update` and `stack sync`.

For the full schema specification including all field constraints and a worked example, see `references/manifest-schema.md`.

## Final report to user

After everything succeeds, tell the user:
- The list of branches created, in order
- The backup ref name in case they want to revert
- That the manifest was written to `.git/stack/manifests/<stack_prefix>.json`
- A visual view of the result by running `git branchless smartlog` (often aliased as `git sl`, but do not assume the alias exists), which shows the stack structure at a glance
- A suggested next step: review the stack contents with `git log --oneline --graph <prefix>-1..<prefix>-N` or visually with `git branchless smartlog`

Do not push the branches. Do not delete the original feature branch. The user keeps full control over what happens next.

## Failure modes and recovery

If anything goes wrong mid-construction (a checkout fails, a commit fails, the user interrupts):

1. Note which phase you were in.
2. Offer the user two recovery paths:
   - **`git undo`** for walking back the operations git-branchless recorded. This is the fastest path for recent failures and the preferred option when it's available.
   - **`git reset --hard refs/backup/<name>`** plus manual cleanup of partial stack branches. This is the belt-and-suspenders option that works regardless of git-branchless state.
3. If proceeding with manual cleanup: hide partial stack commits with `git hide` and delete partial branch refs with `git branch -D`.
4. Confirm the original feature branch is untouched (`git rev-parse <feature-branch>` should match the original tip recorded in phase 2).
5. Remind the user of the backup ref name.
6. Do not retry automatically. Report what happened and let the user decide.

## Things to refuse

- Operating without git-branchless installed and initialized (this is a hard prerequisite)
- Operating on a dirty working tree
- Operating without a clean base branch reference (e.g., if `main` doesn't exist locally)
- Force-pushing or modifying remote refs (this skill is local-only)
- Skipping the verification step "just this once"
- Proceeding when verification fails
- Modifying the original feature branch in any way

The original feature branch is sacred until verification passes and the user explicitly chooses to replace it.