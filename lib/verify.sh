# shellcheck shell=bash
# Tree-hash verification. The "reference tip" is computed by cherry-picking
# the recorded commit SHAs in order onto a base in a throwaway worktree;
# verification compares its tree against the actual stack tip.

if [[ -n "${_STACK_VERIFY_SH:-}" ]]; then
    return 0
fi
_STACK_VERIFY_SH=1

# verify::compute_reference_tip <base_commit> <commit_sha>...
# Echoes the tree SHA produced by cherry-picking each commit onto base in
# order. On unresolvable conflict, dies (the caller should not have invoked
# this if the rebuild hit conflicts: verification runs AFTER conflicts are
# resolved interactively, against the recorded shas).
verify::compute_reference_tip() {
    local base="$1"; shift
    local tmpdir worktree
    tmpdir="$(stack::tmpdir)"
    worktree="$tmpdir/verify-$$.$RANDOM"

    git worktree add --detach --quiet "$worktree" "$base"
    # shellcheck disable=SC2064
    trap "git worktree remove --force '$worktree' >/dev/null 2>&1 || true" RETURN

    local sha
    (
        cd "$worktree"
        for sha in "$@"; do
            if ! git cherry-pick --allow-empty --keep-redundant-commits "$sha" >/dev/null 2>&1; then
                git cherry-pick --abort >/dev/null 2>&1 || true
                stack::die "verify::compute_reference_tip: cherry-pick of $sha onto $base failed; cannot compute reference tree"
            fi
        done
        git rev-parse 'HEAD^{tree}'
    )
}

# verify::verify_tip <expected_tree> <actual_ref>
# Returns 0 if trees equal, prints a clear diff and returns 1 otherwise.
verify::verify_tip() {
    local expected="$1" actual_ref="$2"
    local actual
    actual="$(git::tree_of "$actual_ref")"
    if [[ "$expected" == "$actual" ]]; then
        stack::ok "verification passed: tree=$expected"
        return 0
    fi
    stack::err "verification failed"
    stack::err "  expected tree: $expected"
    stack::err "  actual tree  : $actual ($actual_ref)"
    stack::err "  diff (expected -> actual):"
    git diff-tree -r --stat "$expected" "$actual" >&2 || true
    return 1
}
