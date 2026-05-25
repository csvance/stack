# shellcheck shell=bash
# Thin wrappers over git plumbing. All functions are deterministic and avoid
# touching working tree state unless explicitly noted.

if [[ -n "${_STACK_GIT_HELPERS_SH:-}" ]]; then
    return 0
fi
_STACK_GIT_HELPERS_SH=1

git::tree_of() {
    git rev-parse --verify "$1^{tree}"
}

git::sha_of() {
    git rev-parse --verify "$1^{commit}"
}

git::ref_exists() {
    git show-ref --verify --quiet "$1"
}

git::branch_exists() {
    git::ref_exists "refs/heads/$1"
}

git::is_ancestor() {
    git merge-base --is-ancestor "$1" "$2"
}

git::trees_equal() {
    local a b
    a="$(git::tree_of "$1")" || return 2
    b="$(git::tree_of "$2")" || return 2
    [[ "$a" == "$b" ]]
}

git::current_branch() {
    if ! git symbolic-ref --short --quiet HEAD; then
        return 1
    fi
}

# 0 if working tree is clean of staged/unstaged changes AND there is no
# in-progress merge/rebase/cherry-pick/revert state.
git::working_tree_clean() {
    [[ -z "$(git status --porcelain --untracked-files=no)" ]] || return 1
    local dir
    dir="$(git rev-parse --git-dir)"
    for marker in MERGE_HEAD REBASE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
        [[ ! -e "$dir/$marker" ]] || return 1
    done
    [[ ! -d "$dir/rebase-merge" && ! -d "$dir/rebase-apply" ]]
}

git::oneline_range() {
    git log --no-merges --pretty='format:%h %s' "$1"
}

# git::diff_treeish <a> <b>: human diff-tree summary; echoes status\tpath per line.
git::diff_treeish() {
    git diff-tree -r --name-status "$1" "$2"
}

# git::pretty_summary <commit>
git::pretty_summary() {
    git log -1 --pretty='format:%h %s' "$1"
}
