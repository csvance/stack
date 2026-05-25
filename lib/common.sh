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
# STACK_DRY_RUN, STACK_VERBOSE, STACK_STACK_PREFIX. Leftover positional args
# are written to the STACK_REMAINING_ARGS array.
stack::parse_global_flags() {
    STACK_YES="${STACK_YES:-0}"
    STACK_DRY_RUN="${STACK_DRY_RUN:-0}"
    STACK_VERBOSE="${STACK_VERBOSE:-0}"
    STACK_STACK_PREFIX="${STACK_STACK_PREFIX:-}"
    STACK_REMAINING_ARGS=()

    while (( $# > 0 )); do
        case "$1" in
            --yes|-y)         STACK_YES=1 ;;
            --dry-run|-n)     STACK_DRY_RUN=1 ;;
            --verbose|-v)     STACK_VERBOSE=1 ;;
            --stack)
                shift
                [[ $# -gt 0 ]] || stack::die "--stack requires a prefix argument"
                STACK_STACK_PREFIX="$1"
                ;;
            --stack=*)        STACK_STACK_PREFIX="${1#--stack=}" ;;
            --)               shift; STACK_REMAINING_ARGS+=("$@"); return 0 ;;
            *)                STACK_REMAINING_ARGS+=("$1") ;;
        esac
        shift
    done
}

# Asserts cwd is inside a git worktree and exports STACK_REPO_ROOT, STACK_GIT_DIR.
# Migrates a legacy repo-root stack-manifest.json into .git/stack/manifests/ if
# present. Does not resolve STACK_MANIFEST; subcommands call stack::resolve_manifest
# when they need one.
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

    STACK_MANIFESTS_DIR="$STACK_GIT_DIR/stack/manifests"
    export STACK_MANIFESTS_DIR

    stack::migrate_legacy_manifest

    # Pin the tmpdir path now (created lazily by stack::tmpdir) and register
    # the cleanup trap in the main shell so it survives across $(...) calls.
    STACK_TMPDIR="$STACK_GIT_DIR/stack-tmp/$$"
    export STACK_TMPDIR
    trap 'stack::cleanup_tmpdir' EXIT
}

# One-time migration: if a legacy <repo-root>/stack-manifest.json exists, move
# it to .git/stack/manifests/<stack_prefix>.json. Idempotent and silent when
# no legacy file is present.
stack::migrate_legacy_manifest() {
    local legacy="$STACK_REPO_ROOT/stack-manifest.json"
    [[ -f "$legacy" ]] || return 0

    local prefix
    prefix="$(jq -r '.stack_prefix // empty' "$legacy" 2>/dev/null || true)"
    if [[ -z "$prefix" ]]; then
        stack::warn "legacy stack-manifest.json has no stack_prefix; leaving in place"
        return 0
    fi

    local new_path="$STACK_MANIFESTS_DIR/${prefix}.json"
    if [[ -f "$new_path" ]]; then
        stack::warn "legacy $legacy and $new_path both exist; leaving legacy in place to avoid clobbering"
        return 0
    fi

    mkdir -p "$STACK_MANIFESTS_DIR"
    mv "$legacy" "$new_path"
    stack::info "migrated legacy stack-manifest.json -> $new_path"
}

# Resolve STACK_MANIFEST. If STACK_MANIFEST is already set (e.g. by a test),
# returns immediately. Otherwise picks the manifest matching --stack <prefix>
# or, failing that, the manifest whose branches contain the current branch.
# Errors via stack::die on no match or ambiguity.
stack::resolve_manifest() {
    [[ -n "${STACK_MANIFEST:-}" ]] && { export STACK_MANIFEST; return 0; }

    local dir="$STACK_MANIFESTS_DIR"
    if [[ ! -d "$dir" ]]; then
        stack::die "no stacks in this repo (no $dir); run the decomposer first"
    fi

    if [[ -n "${STACK_STACK_PREFIX:-}" ]]; then
        local p="$dir/${STACK_STACK_PREFIX}.json"
        [[ -f "$p" ]] || stack::die "no stack matching --stack=$STACK_STACK_PREFIX (looked at $p); run 'stack list' to see available stacks"
        STACK_MANIFEST="$p"
        export STACK_MANIFEST
        return 0
    fi

    local cur_branch
    cur_branch="$(git symbolic-ref --short --quiet HEAD 2>/dev/null || true)"
    if [[ -z "$cur_branch" ]]; then
        stack::die "HEAD is detached and --stack not given; cannot select a stack"
    fi

    shopt -s nullglob
    local matches=() m
    for m in "$dir"/*.json; do
        if jq -e --arg b "$cur_branch" '.branches | any(.name == $b)' "$m" >/dev/null 2>&1; then
            matches+=("$m")
        fi
    done
    shopt -u nullglob

    case "${#matches[@]}" in
        0)  stack::die "branch '$cur_branch' is not a member of any stack; use --stack <prefix> or 'stack list' to see available stacks" ;;
        1)  STACK_MANIFEST="${matches[0]}" ;;
        *)  stack::err "branch '$cur_branch' belongs to multiple stacks; use --stack <prefix> to disambiguate:"
            for m in "${matches[@]}"; do
                stack::err "  --stack=$(jq -r '.stack_prefix' "$m")"
            done
            stack::die "ambiguous stack selection"
            ;;
    esac
    export STACK_MANIFEST
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
