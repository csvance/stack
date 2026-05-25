# shellcheck shell=bash
# Azure DevOps wrappers via the `az repos pr` CLI.

if [[ -n "${_STACK_AZ_HELPERS_SH:-}" ]]; then
    return 0
fi
_STACK_AZ_HELPERS_SH=1

az::preflight() {
    stack::require_cmd az
    if ! az extension show --name azure-devops >/dev/null 2>&1; then
        stack::die "azure-devops extension missing; run 'az extension add --name azure-devops'"
    fi
}

# Verifies the configured repo is reachable with the current credentials.
# Works for both Azure DevOps Services (az login / AAD) and on-prem
# Azure DevOps Server (az devops login with a PAT). Probes the actual repo
# so we exercise the same scope ("Code (read)") that PR operations require.
# Requires az::resolve_repo to have populated AZ_* first.
az::preflight_auth() {
    [[ -n "${AZ_ORG_URL:-}" && -n "${AZ_PROJECT:-}" && -n "${AZ_REPO:-}" ]] \
        || stack::die "az::preflight_auth: AZ_* not set (call az::resolve_repo first)"
    if ! az repos show \
            --organization "$AZ_ORG_URL" \
            --project "$AZ_PROJECT" \
            --repository "$AZ_REPO" \
            --only-show-errors -o none >/dev/null 2>&1; then
        stack::die "az cannot reach $AZ_ORG_URL/$AZ_PROJECT/$AZ_REPO with the current credentials; for Azure DevOps cloud run 'az login', for on-prem run 'az devops login --organization $AZ_ORG_URL' with a PAT that has Code (read, write) scope"
    fi
}

# Parses the origin remote into AZ_ORG_URL, AZ_PROJECT, AZ_REPO.
# Supported URL forms:
#   https://dev.azure.com/<org>/<project>/_git/<repo>
#   https://<user>@dev.azure.com/<org>/<project>/_git/<repo>
#   git@ssh.dev.azure.com:v3/<org>/<project>/<repo>
#   https://<org>.visualstudio.com/<project>/_git/<repo>
#   https://<host>[/<path>...]/<project>/_git/<repo>  (Azure DevOps Server / on-prem)
az::resolve_repo() {
    local url
    url="$(git remote get-url origin 2>/dev/null)" || stack::die "no 'origin' remote configured"

    local org='' project='' repo=''
    if [[ "$url" =~ ^git@ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/?#]+) ]]; then
        # SSH form is a special snowflake; no /_git/ marker, so handle it first.
        org="${BASH_REMATCH[1]}"
        project="${BASH_REMATCH[2]}"
        repo="${BASH_REMATCH[3]}"
        AZ_ORG_URL="https://dev.azure.com/$org"
        AZ_PROJECT="$project"
    elif [[ "$url" =~ ^(https?://[^[:space:]]+)/_git/([^/?#]+) ]]; then
        # Any HTTPS form with /_git/<repo>. The path segment immediately before
        # /_git/ is the project; everything else is the org URL.
        local left="${BASH_REMATCH[1]}"
        repo="${BASH_REMATCH[2]}"
        project="${left##*/}"
        AZ_ORG_URL="${left%/*}"
        AZ_PROJECT="$project"
        # Strip any embedded user info from the host (e.g. https://user@host/...).
        if [[ "$AZ_ORG_URL" =~ ^(https?://)[^/]*@(.*)$ ]]; then
            AZ_ORG_URL="${BASH_REMATCH[1]}${BASH_REMATCH[2]}"
        fi
    else
        stack::die "could not parse Azure DevOps URL from origin remote: $url"
    fi

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
