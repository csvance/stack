# shellcheck shell=bash
# Backup snapshot family under refs/backup/stack/<prefix>/<op>-<UTC>/<branch>.
# A "snapshot set" is the group of refs sharing the same <op>-<UTC> prefix.

if [[ -n "${_STACK_BACKUP_SH:-}" ]]; then
    return 0
fi
_STACK_BACKUP_SH=1

backup::_prefix() {
    manifest::get '.stack_prefix'
}

backup::_now_utc() {
    date -u +%Y%m%dT%H%M%SZ
}

# backup::snapshot <op> <branch>...
# Creates a snapshot set and echoes the set ID
# (refs/backup/stack/<prefix>/<op>-<ts>). Each branch's current SHA is
# captured under <set>/<branch>.
backup::snapshot() {
    local op="$1"; shift
    [[ $# -gt 0 ]] || stack::die "backup::snapshot requires at least one branch"

    local prefix ts set_id
    prefix="$(backup::_prefix)"
    ts="$(backup::_now_utc)"
    set_id="refs/backup/stack/$prefix/${op}-${ts}"

    local branch sha
    for branch in "$@"; do
        if ! sha="$(git rev-parse --verify "refs/heads/$branch" 2>/dev/null)"; then
            stack::warn "backup::snapshot: branch '$branch' not found, skipping"
            continue
        fi
        git update-ref "$set_id/$branch" "$sha"
        stack::debug "snapshot $branch @ $sha -> $set_id/$branch"
    done

    printf '%s\n' "$set_id"
}

# backup::list_sets: echo set IDs newest first, one per line.
backup::list_sets() {
    local prefix
    prefix="$(backup::_prefix)"
    git for-each-ref --format='%(refname)' "refs/backup/stack/$prefix/" \
        | awk -F/ '{
            # strip the trailing /<branch>: set id is everything up to but not including
            # the last segment.
            n=NF;
            out="";
            for (i=1; i<n; i++) {
                out = (i==1 ? $i : out "/" $i);
            }
            print out
        }' \
        | sort -u \
        | sort -t- -k2,2r
}

# backup::set_branches <set-id>: echoes <branch>\t<sha> for every ref in the set.
backup::set_branches() {
    local set_id="$1"
    git for-each-ref --format='%(refname)%09%(objectname)' "$set_id/" \
        | awk -F'\t' -v prefix="$set_id/" '{
            sub(prefix, "", $1); print $1 "\t" $2
        }'
}

# backup::describe <set-id>: human-readable summary of a set.
backup::describe() {
    local set_id="$1"
    local tail="${set_id##*/}"             # e.g. update-20260524T161205Z
    local op="${tail%-*}"
    local ts="${tail##*-}"
    local count
    count="$(git for-each-ref "$set_id/" | wc -l | tr -d ' ')"
    printf '%s\n' "$set_id"
    printf '  op:        %s\n' "$op"
    printf '  timestamp: %s\n' "$ts"
    printf '  branches:  %s\n' "$count"
    while IFS=$'\t' read -r branch sha; do
        printf '    %s -> %s\n' "$branch" "$sha"
    done < <(backup::set_branches "$set_id")
}

# backup::restore <set-id>: reset each branch in the set to its recorded SHA.
# If HEAD is on one of the branches being restored, hard-reset the working
# tree.
backup::restore() {
    local set_id="$1"
    [[ -n "$set_id" ]] || stack::die "backup::restore requires a set-id"
    if [[ "$(backup::set_branches "$set_id" | wc -l | tr -d ' ')" == 0 ]]; then
        stack::die "backup set has no refs: $set_id"
    fi

    local cur_branch=''
    cur_branch="$(git::current_branch 2>/dev/null || true)"

    local branch sha hit_current=0
    while IFS=$'\t' read -r branch sha; do
        if [[ "$branch" == "$cur_branch" ]]; then
            hit_current=1
            continue
        fi
        if git::branch_exists "$branch"; then
            git update-ref "refs/heads/$branch" "$sha"
        else
            git branch "$branch" "$sha"
        fi
        stack::ok "restored $branch -> $sha"
    done < <(backup::set_branches "$set_id")

    if (( hit_current )); then
        local cur_sha
        cur_sha="$(git for-each-ref --format='%(objectname)' "$set_id/$cur_branch")"
        git reset --hard "$cur_sha"
        stack::ok "restored current branch $cur_branch -> $cur_sha (working tree reset)"
    fi
}

backup::latest_set() {
    backup::list_sets | head -n 1
}
