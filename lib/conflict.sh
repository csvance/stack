# shellcheck shell=bash
# PyCharm-driven 3-way merge conflict resolution.

if [[ -n "${_STACK_CONFLICT_SH:-}" ]]; then
    return 0
fi
_STACK_CONFLICT_SH=1

# conflict::pycharm_bin: discover the PyCharm launcher and cache in
# STACK_PYCHARM. Order: explicit env, PATH, Linux common paths, macOS paths.
conflict::pycharm_bin() {
    if [[ -n "${STACK_PYCHARM:-}" && -x "$STACK_PYCHARM" ]]; then
        printf '%s\n' "$STACK_PYCHARM"
        return 0
    fi
    if command -v pycharm >/dev/null 2>&1; then
        STACK_PYCHARM="$(command -v pycharm)"
        export STACK_PYCHARM
        printf '%s\n' "$STACK_PYCHARM"
        return 0
    fi
    local candidates=(
        /opt/pycharm-*/bin/pycharm.sh
        /snap/pycharm-professional/current/bin/pycharm.sh
        /snap/pycharm-community/current/bin/pycharm.sh
        /usr/share/pycharm/bin/pycharm.sh
        '/Applications/PyCharm.app/Contents/MacOS/pycharm'
        '/Applications/PyCharm Professional.app/Contents/MacOS/pycharm'
        '/Applications/PyCharm CE.app/Contents/MacOS/pycharm'
    )
    local c
    for c in "${candidates[@]}"; do
        if [[ -x "$c" ]]; then
            STACK_PYCHARM="$c"
            export STACK_PYCHARM
            printf '%s\n' "$STACK_PYCHARM"
            return 0
        fi
    done
    stack::die "pycharm launcher not found. Install PyCharm and ensure 'pycharm' is on PATH, or set STACK_PYCHARM."
}

# conflict::_extract_stage <stage> <path> <out-tmp>
# Writes the indexed blob at <stage> for <path> to <out-tmp>. Returns 1 if
# the stage is absent (e.g. delete/modify conflict). Returns 0 on success.
conflict::_extract_stage() {
    local stage="$1" path="$2" out="$3"
    local raw
    if ! raw="$(git checkout-index --stage="$stage" --temp -- "$path" 2>/dev/null)"; then
        return 1
    fi
    local tmpname="${raw%%$'\t'*}"
    [[ -n "$tmpname" && -f "$tmpname" ]] || return 1
    mv "$tmpname" "$out"
}

# conflict::_has_markers <file>: returns 0 if conflict markers remain.
conflict::_has_markers() {
    grep -Eq '^(<<<<<<< |======= |>>>>>>> )' "$1"
}

# conflict::resolve_one <path>: drives PyCharm for a single conflicted path.
# Loops on the user's choice. Returns 0 on resolve, 1 on user-abort.
conflict::resolve_one() {
    local path="$1"
    local pyc
    pyc="$(conflict::pycharm_bin)"

    local td
    td="$(stack::tmpdir)/conflict-$$.$RANDOM"
    mkdir -p "$td"

    local base="$td/base" local_f="$td/local" remote="$td/remote" output="$td/output"
    local have_base=0 have_local=0 have_remote=0
    conflict::_extract_stage 1 "$path" "$base"     && have_base=1   || true
    conflict::_extract_stage 2 "$path" "$local_f"  && have_local=1  || true
    conflict::_extract_stage 3 "$path" "$remote"   && have_remote=1 || true

    if (( have_local == 0 || have_remote == 0 )); then
        # delete/modify or add/add conflict: 4-arg merge doesn't fit. Prompt.
        stack::warn "delete/modify conflict on '$path' (stages present: base=$have_base local=$have_local remote=$have_remote)"
        local choice
        choice="$(prompt::choice "Resolve '$path' how?" "k/d/s/a")"
        case "$choice" in
            k)
                # Keep the modified side. Take whichever of local/remote exists.
                if (( have_local )); then cp "$local_f" "$path"; else cp "$remote" "$path"; fi
                git add "$path"
                return 0
                ;;
            d)
                git rm -f -- "$path" >/dev/null
                return 0
                ;;
            s)
                stack::warn "skipping '$path' (will fail the resolve_pending check)"
                return 0
                ;;
            a)
                return 1
                ;;
        esac
    fi

    # Seed the output with the working-tree content (which has conflict markers).
    cp -- "$path" "$output"

    while true; do
        stack::info "launching pycharm merge for $path"
        if ! "$pyc" merge "$local_f" "$remote" "$base" "$output" >/dev/null 2>&1; then
            stack::warn "pycharm exited non-zero; checking output anyway"
        fi
        if conflict::_has_markers "$output"; then
            stack::warn "conflict markers remain in '$path' after PyCharm"
            local choice
            choice="$(prompt::choice "Action for '$path'?" "r/s/a")"
            case "$choice" in
                r) continue ;;
                s)
                    cp "$output" "$path"
                    return 0
                    ;;
                a)
                    return 1
                    ;;
            esac
        fi
        cp "$output" "$path"
        git add "$path"
        return 0
    done
}

# conflict::resolve_pending: drive PyCharm for each pending UU/AA/DU/UD/etc.
# Sets STACK_CONFLICTS_SEEN += <count of paths processed>. Returns 0 on
# successful resolution of every path, 1 on any abort or skip.
conflict::resolve_pending() {
    : "${STACK_CONFLICTS_SEEN:=0}"
    local path skipped=0
    while IFS= read -r path; do
        [[ -n "$path" ]] || continue
        STACK_CONFLICTS_SEEN=$(( STACK_CONFLICTS_SEEN + 1 ))
        if ! conflict::resolve_one "$path"; then
            stack::err "user aborted conflict resolution for $path"
            return 1
        fi
        if [[ -f "$path" ]] && conflict::_has_markers "$path"; then
            skipped=1
        fi
    done < <(git diff --name-only --diff-filter=U)

    if (( skipped )); then
        stack::err "one or more files still contain conflict markers"
        return 1
    fi
    # Confirm git's index is now clean of unmerged paths.
    if [[ -n "$(git diff --name-only --diff-filter=U)" ]]; then
        stack::err "git index still reports unmerged paths"
        return 1
    fi
    return 0
}

# conflict::drain_during_branchless_move <src> <dest>: invoke branchless::move
# and, if it pauses on a conflict, drive PyCharm + continue until done.
# Sets STACK_CONFLICTS_SEEN >= count of resolved paths. Returns 0 on success,
# 1 on user-abort.
conflict::drain_during_branchless_move() {
    local src="$1" dest="$2"
    : "${STACK_CONFLICTS_SEEN:=0}"

    if branchless::move "$src" "$dest"; then
        return 0
    fi

    # The move paused on a conflict. Drive resolution + continue in a loop.
    while true; do
        if ! conflict::resolve_pending; then
            branchless::abort_op
            return 1
        fi
        if branchless::continue_op; then
            return 0
        fi
        # Conflict in the next commit; loop and resolve again.
        if [[ -z "$(git diff --name-only --diff-filter=U)" ]]; then
            # No unmerged paths but continue still failed; bail.
            branchless::abort_op
            stack::err "branchless continue failed with no unmerged paths to resolve"
            return 1
        fi
    done
}
