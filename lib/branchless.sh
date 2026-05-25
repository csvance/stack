# shellcheck shell=bash
# git-branchless wrappers. Preflight + the small set of branchless commands
# the CLI relies on.

if [[ -n "${_STACK_BRANCHLESS_SH:-}" ]]; then
    return 0
fi
_STACK_BRANCHLESS_SH=1

branchless::preflight() {
    if ! git branchless --version >/dev/null 2>&1; then
        stack::die "git-branchless is required but not installed. See https://github.com/arxanas/git-branchless"
    fi
    if ! git branchless query 'all()' >/dev/null 2>&1; then
        stack::die "git-branchless is installed but not initialized. Run: git branchless init"
    fi
}

# branchless::move <src_branch> <dest>
# Moves src and all descendants onto dest. Passes --force-rewrite because
# stack branches are typically already pushed (which branchless treats as
# 'public'); in a stacked-diff workflow we rewrite them explicitly. Passes
# --merge so conflicts pause the on-disk rebase rather than aborting.
branchless::move() {
    local src="$1" dest="$2"
    git branchless move \
        --source "$src" \
        --dest "$dest" \
        --force-rewrite \
        --merge
}

branchless::smartlog() {
    git branchless smartlog "$@"
}

branchless::continue_op() {
    # branchless 0.11 has no 'continue' subcommand. After a conflict during
    # `git branchless move --merge`, the operation falls back to an on-disk
    # rebase that is continued with `git rebase --continue`.
    git rebase --continue
}

branchless::abort_op() {
    # Best-effort abort: try the underlying git ops based on the in-progress
    # state file.
    local dir; dir="$(git rev-parse --git-dir)"
    if [[ -d "$dir/rebase-merge" || -d "$dir/rebase-apply" ]]; then
        git rebase --abort 2>/dev/null || true
    elif [[ -f "$dir/CHERRY_PICK_HEAD" ]]; then
        git cherry-pick --abort 2>/dev/null || true
    elif [[ -f "$dir/MERGE_HEAD" ]]; then
        git merge --abort 2>/dev/null || true
    fi
}

branchless::undo_hint() {
    stack::info "If you need to recover but no backup matches, try: git undo"
}
