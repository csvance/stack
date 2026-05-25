# shellcheck shell=bash
# Sourced by every subcommand. Provides logging, error handling, and global
# flag parsing. Idempotent: safe to source twice.

if [[ -n "${_STACK_COMMON_SH:-}" ]]; then
    return 0
fi
_STACK_COMMON_SH=1

if [[ -z "${BASH_VERSION:-}" ]]; then
    echo "stack: requires bash, not /bin/sh" >&2
    exit 1
fi
if (( BASH_VERSINFO[0] < 4 )); then
    {
        echo "stack: requires bash 4 or newer; this is bash ${BASH_VERSION}"
        echo "  macOS: brew install bash, then update PATH so 'bash' resolves to /opt/homebrew/bin/bash or /usr/local/bin/bash"
        echo "  (the /usr/bin/env bash shebang then picks up the newer interpreter)"
    } >&2
    exit 1
fi

set -euo pipefail
IFS=$'\n\t'

# Color helpers honor NO_COLOR and non-TTY stderr.
if [[ -t 2 ]] && [[ -z "${NO_COLOR:-}" ]]; then
    _STACK_C_RED=$'\e[31m'
    _STACK_C_YELLOW=$'\e[33m'
    _STACK_C_GREEN=$'\e[32m'
    _STACK_C_DIM=$'\e[2m'
    _STACK_C_RESET=$'\e[0m'
else
    _STACK_C_RED=''
    _STACK_C_YELLOW=''
    _STACK_C_GREEN=''
    _STACK_C_DIM=''
    _STACK_C_RESET=''
fi

stack::log() {
    local level="$1"; shift
    local color=''
    case "$level" in
        err) color="$_STACK_C_RED" ;;
        warn) color="$_STACK_C_YELLOW" ;;
        ok) color="$_STACK_C_GREEN" ;;
        debug) color="$_STACK_C_DIM" ;;
    esac
    printf '%sstack[%s]:%s %s\n' "$color" "$level" "$_STACK_C_RESET" "$*" >&2
}

stack::info() { stack::log info "$@"; }
stack::warn() { stack::log warn "$@"; }
stack::err()  { stack::log err  "$@"; }
stack::ok()   { stack::log ok   "$@"; }
stack::debug() {
    [[ "${STACK_VERBOSE:-0}" == 1 ]] || return 0
    stack::log debug "$@"
}

stack::die() {
    stack::err "$@"
    exit 1
}

stack::require_cmd() {
    local missing=()
    for cmd in "$@"; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        stack::die "missing required commands: ${missing[*]}"
    fi
}

# Global flag parsing. Reads the subcommand argv array, sets STACK_YES,
# STACK_DRY_RUN, STACK_VERBOSE, STACK_STRUCTURED, STACK_MANIFEST. Leftover
# positional args are written to the STACK_REMAINING_ARGS array.
stack::parse_global_flags() {
    STACK_YES="${STACK_YES:-0}"
    STACK_DRY_RUN="${STACK_DRY_RUN:-0}"
    STACK_VERBOSE="${STACK_VERBOSE:-0}"
    STACK_STRUCTURED="${STACK_STRUCTURED:-0}"
    STACK_MANIFEST="${STACK_MANIFEST:-}"
    STACK_REMAINING_ARGS=()

    while (( $# > 0 )); do
        case "$1" in
            --yes|-y)         STACK_YES=1 ;;
            --dry-run|-n)     STACK_DRY_RUN=1 ;;
            --verbose|-v)     STACK_VERBOSE=1 ;;
            --structured)     STACK_STRUCTURED=1 ;;
            --manifest)
                shift
                [[ $# -gt 0 ]] || stack::die "--manifest requires a path argument"
                STACK_MANIFEST="$1"
                ;;
            --manifest=*)     STACK_MANIFEST="${1#--manifest=}" ;;
            --)               shift; STACK_REMAINING_ARGS+=("$@"); return 0 ;;
            *)                STACK_REMAINING_ARGS+=("$1") ;;
        esac
        shift
    done
}

# Asserts cwd is inside a git worktree and exports STACK_REPO_ROOT, STACK_GIT_DIR.
stack::preflight_repo() {
    stack::require_cmd git jq
    if ! STACK_REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
        stack::die "not inside a git work tree"
    fi
    STACK_GIT_DIR="$(git rev-parse --git-dir)"
    if [[ "$STACK_GIT_DIR" != /* ]]; then
        STACK_GIT_DIR="$STACK_REPO_ROOT/$STACK_GIT_DIR"
    fi
    export STACK_REPO_ROOT STACK_GIT_DIR

    if [[ -z "$STACK_MANIFEST" ]]; then
        STACK_MANIFEST="$STACK_REPO_ROOT/stack-manifest.json"
    fi
    export STACK_MANIFEST

    # Pin the tmpdir path now (created lazily by stack::tmpdir) and register
    # the cleanup trap in the main shell so it survives across $(...) calls.
    STACK_TMPDIR="$STACK_GIT_DIR/stack-tmp/$$"
    export STACK_TMPDIR
    trap 'stack::cleanup_tmpdir' EXIT
}

# Per-invocation tmpdir under .git/stack-tmp/<pid>. The path is set during
# stack::preflight_repo so the EXIT trap registers in the main shell (not in
# a command-substitution subshell, where it would fire immediately and delete
# the directory). stack::tmpdir is idempotent and safe to call from $(...).
stack::tmpdir() {
    [[ -n "${STACK_TMPDIR:-}" ]] || stack::die "stack::tmpdir requires stack::preflight_repo first"
    mkdir -p "$STACK_TMPDIR"
    printf '%s\n' "$STACK_TMPDIR"
}

stack::cleanup_tmpdir() {
    if [[ -n "${STACK_TMPDIR:-}" && -d "$STACK_TMPDIR" ]]; then
        rm -rf "$STACK_TMPDIR"
    fi
}
