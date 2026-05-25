# shellcheck shell=bash
# Azure DevOps wrappers via the `az repos pr` CLI.

if [[ -n "${_STACK_AZ_HELPERS_SH:-}" ]]; then
    return 0
fi
_STACK_AZ_HELPERS_SH=1

az::preflight() {
    stack::require_cmd az
    if ! az account show --only-show-errors -o none >/dev/null 2>&1; then
        stack::die "az is not signed in; run 'az login' (and ensure the azure-devops extension is installed)"
    fi
    if ! az extension show --name azure-devops >/dev/null 2>&1; then
        stack::die "azure-devops extension missing; run 'az extension add --name azure-devops'"
    fi
}

# Parses the origin remote into AZ_ORG_URL, AZ_PROJECT, AZ_REPO.
# Supported URL forms:
#   https://dev.azure.com/<org>/<project>/_git/<repo>
#   https://<user>@dev.azure.com/<org>/<project>/_git/<repo>
#   git@ssh.dev.azure.com:v3/<org>/<project>/<repo>
#   https://<org>.visualstudio.com/<project>/_git/<repo>
az::resolve_repo() {
    local url
    url="$(git remote get-url origin 2>/dev/null)" || stack::die "no 'origin' remote configured"

    local org='' project='' repo=''
    if [[ "$url" =~ ^https?://([^/@]+@)?dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/?#]+) ]]; then
        org="${BASH_REMATCH[2]}"
        project="${BASH_REMATCH[3]}"
        repo="${BASH_REMATCH[4]}"
        AZ_ORG_URL="https://dev.azure.com/$org"
    elif [[ "$url" =~ ^git@ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/?#]+) ]]; then
        org="${BASH_REMATCH[1]}"
        project="${BASH_REMATCH[2]}"
        repo="${BASH_REMATCH[3]}"
        AZ_ORG_URL="https://dev.azure.com/$org"
    elif [[ "$url" =~ ^https?://([^/.]+)\.visualstudio\.com/([^/]+)/_git/([^/?#]+) ]]; then
        org="${BASH_REMATCH[1]}"
        project="${BASH_REMATCH[2]}"
        repo="${BASH_REMATCH[3]}"
        AZ_ORG_URL="https://$org.visualstudio.com"
    else
        stack::die "could not parse Azure DevOps URL from origin remote: $url"
    fi

    AZ_PROJECT="$project"
    AZ_REPO="${repo%.git}"
    export AZ_ORG_URL AZ_PROJECT AZ_REPO

    stack::debug "azure devops: org=$AZ_ORG_URL project=$AZ_PROJECT repo=$AZ_REPO"
}

# az::_with_defaults <cmd>...: runs az with --organization $AZ_ORG_URL --project $AZ_PROJECT.
az::_with_defaults() {
    az "$@" --organization "$AZ_ORG_URL"
}

# az::pr_list_for_branch <branch>: returns JSON array of PRs whose source is <branch>.
az::pr_list_for_branch() {
    local branch="$1"
    az::_with_defaults repos pr list \
        --project "$AZ_PROJECT" \
        --repository "$AZ_REPO" \
        --source-branch "refs/heads/$branch" \
        --status all \
        --output json 2>/dev/null
}

# az::pr_show <pr_id>: returns JSON.
az::pr_show() {
    local pr_id="$1"
    az::_with_defaults repos pr show --id "$pr_id" --output json 2>/dev/null
}

# az::pr_create <source> <target> <title> <description-file>: echoes JSON.
az::pr_create() {
    local source="$1" target="$2" title="$3" desc_file="$4"
    az::_with_defaults repos pr create \
        --project "$AZ_PROJECT" \
        --repository "$AZ_REPO" \
        --source-branch "refs/heads/$source" \
        --target-branch "refs/heads/$target" \
        --title "$title" \
        --description @"$desc_file" \
        --output json
}

# az::pr_update <pr_id> [--title TITLE] [--description-file FILE] [--target TARGET] [--status STATUS]
az::pr_update() {
    local pr_id="$1"; shift
    local -a args=()
    while (( $# > 0 )); do
        case "$1" in
            --title)            args+=(--title "$2"); shift 2 ;;
            --description-file) args+=(--description @"$2"); shift 2 ;;
            --target)           args+=(--target-branch "refs/heads/$2"); shift 2 ;;
            --status)           args+=(--status "$2"); shift 2 ;;
            *)                  stack::die "az::pr_update: unknown flag $1" ;;
        esac
    done
    az::_with_defaults repos pr update --id "$pr_id" "${args[@]}" --output json
}

# az::pr_url_from_id <pr_id>: synthesize the human URL.
az::pr_url_from_id() {
    local pr_id="$1"
    printf '%s/%s/_git/%s/pullrequest/%s\n' "$AZ_ORG_URL" "$AZ_PROJECT" "$AZ_REPO" "$pr_id"
}

# az::refresh_status_cache <pr_id>...: builds the pr_status_cache JSON.
az::refresh_status_cache() {
    local now
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local entries='{}'
    local pr_id status
    for pr_id in "$@"; do
        [[ -n "$pr_id" && "$pr_id" != "null" ]] || continue
        status="$(az::pr_show "$pr_id" | jq -r '.status // "unknown"')"
        entries="$(jq -c --arg id "$pr_id" --arg s "$status" '. + {($id): $s}' <<<"$entries")"
    done
    jq -c -n --arg ts "$now" --argjson prs "$entries" '{fetched_at: $ts, prs: $prs}'
}
