# shellcheck shell=bash
# One-fact-per-line output for script-to-script consumption. Active only when
# STACK_STRUCTURED=1. All emitters write to stdout; human-facing log lines go
# through stack::log to stderr.

if [[ -n "${_STACK_STRUCTURED_SH:-}" ]]; then
    return 0
fi
_STACK_STRUCTURED_SH=1

stack::s() {
    [[ "${STACK_STRUCTURED:-0}" == 1 ]] || return 0
    printf '%s\n' "$*"
}
