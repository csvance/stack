# shellcheck shell=bash
# Confirmation prompts and dry-run wrappers. Honors STACK_YES and STACK_DRY_RUN
# from common.sh.

if [[ -n "${_STACK_PROMPT_SH:-}" ]]; then
    return 0
fi
_STACK_PROMPT_SH=1

# prompt::confirm <one-line-question>
# Returns 0 to proceed, 1 to abort. Honors STACK_YES (always 0) and
# STACK_DRY_RUN (always 0 with a notice).
prompt::confirm() {
    local question="$1"
    if [[ "${STACK_YES:-0}" == 1 ]]; then
        stack::debug "auto-confirmed: $question"
        return 0
    fi
    if [[ "${STACK_DRY_RUN:-0}" == 1 ]]; then
        stack::info "[dry-run] would prompt: $question"
        return 0
    fi
    local reply
    printf '%s [y/N] ' "$question" >&2
    if ! read -r reply; then
        return 1
    fi
    case "$reply" in
        y|Y|yes|YES) return 0 ;;
        *)           return 1 ;;
    esac
}

# prompt::choice <question> <choice-list>
# choice-list is a space-separated list of single-character labels, e.g.
# "s/a" for squash/additive or "r/s/a" for retry/skip/abort. The first
# character of each label is the accepted key. Echoes the chosen key.
# Under --yes the function errors (no safe default).
prompt::choice() {
    local question="$1" choices="$2"
    if [[ "${STACK_DRY_RUN:-0}" == 1 ]]; then
        stack::info "[dry-run] would prompt: $question [$choices]"
        printf '%s\n' "${choices%%/*}"
        return 0
    fi
    if [[ "${STACK_YES:-0}" == 1 ]]; then
        stack::die "prompt::choice requires interactive input or an explicit flag; question was: $question"
    fi
    local reply
    while true; do
        printf '%s [%s] ' "$question" "$choices" >&2
        if ! read -r reply; then
            return 1
        fi
        reply="$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]' | head -c1)"
        local IFS='/'
        for c in $choices; do
            if [[ "$reply" == "$c" ]]; then
                printf '%s\n' "$reply"
                return 0
            fi
        done
        stack::warn "invalid choice; expected one of: $choices"
    done
}

# prompt::summary_block: read a heredoc on stdin, print a bordered summary
# block to stderr.
prompt::summary_block() {
    local line
    {
        printf -- '----- %s -----\n' "${1:-operation summary}"
        while IFS= read -r line; do
            printf '  %s\n' "$line"
        done
        printf -- '----- end -----\n'
    } >&2
}

# prompt::dry_run_skip <cmd>...: print and skip if dry-run, otherwise exec.
prompt::dry_run_skip() {
    if [[ "${STACK_DRY_RUN:-0}" == 1 ]]; then
        stack::info "[dry-run] $*"
        return 0
    fi
    "$@"
}
