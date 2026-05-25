# shellcheck shell=bash
# stack-manifest.json read/write. All access goes through jq; writes are
# atomic via write-to-temp-then-mv.

if [[ -n "${_STACK_MANIFEST_SH:-}" ]]; then
    return 0
fi
_STACK_MANIFEST_SH=1

manifest::path() {
    [[ -n "${STACK_MANIFEST:-}" ]] || stack::die "STACK_MANIFEST unset; run stack::preflight_repo first"
    [[ -f "$STACK_MANIFEST" ]] || stack::die "stack-manifest.json not found at $STACK_MANIFEST"
    printf '%s\n' "$STACK_MANIFEST"
}

# Validates required fields. Order values must be positive integers and
# unique across branches; gaps are allowed (a stack with branches at orders
# 2, 3, 4 is valid, as happens after a stack-land drops order 1).
manifest::load() {
    local path
    path="$(manifest::path)"

    local errors
    errors="$(
        jq -r '
            def check:
                [
                    (if (.version | type) != "number" then "version: must be a number" elif .version != 1 then "version: only 1 is supported" else empty end),
                    (if (.base_branch  | type) != "string" then "base_branch: must be a string" else empty end),
                    (if (.original_feature_branch | type) != "string" then "original_feature_branch: must be a string" else empty end),
                    (if (.original_tip_commit | type) != "string" then "original_tip_commit: must be a string" else empty end),
                    (if (.stack_prefix | type) != "string" then "stack_prefix: must be a string" else empty end),
                    (if (.branches | type) != "array" or (.branches | length) == 0 then "branches: must be a non-empty array" else empty end),
                    (.branches | to_entries[] |
                        [
                            (if (.value.order | type) != "number" or .value.order < 1 or (.value.order | floor) != .value.order then "branches[\(.key)].order: must be a positive integer" else empty end),
                            (if (.value.name   | type) != "string" then "branches[\(.key)].name: must be string"   else empty end),
                            (if (.value.commit_sha | type) != "string" then "branches[\(.key)].commit_sha: must be string" else empty end),
                            (if (.value.parent_branch | type) != "string" then "branches[\(.key)].parent_branch: must be string" else empty end)
                        ] | .[]
                    ),
                    (if (.branches | map(.order) | length) != (.branches | map(.order) | unique | length) then "branches: order values must be unique" else empty end)
                ] | join("\n");
            check
        ' "$path"
    )"

    if [[ -n "$errors" ]]; then
        stack::die "manifest invalid:"$'\n'"$errors"
    fi
}

manifest::get() {
    local filter="$1"
    jq -r "$filter" "$(manifest::path)"
}

manifest::get_json() {
    local filter="$1"
    jq -c "$filter" "$(manifest::path)"
}

# Echoes lines: <order>\t<name>\t<commit_sha>\t<parent_branch>
manifest::branches_in_order() {
    jq -r '.branches | sort_by(.order) | .[] | [.order, .name, .commit_sha, .parent_branch] | @tsv' "$(manifest::path)"
}

manifest::branch_field() {
    local name="$1" field="$2"
    jq -r --arg n "$name" --arg f "$field" '.branches[] | select(.name == $n) | .[$f] // empty' "$(manifest::path)"
}

# Atomic in-place edit. Filter is a jq expression evaluated against the
# current manifest; result is written back via temp+mv.
manifest::edit() {
    local filter="$1"
    local path tmp
    path="$(manifest::path)"
    tmp="$(mktemp "${path}.XXXXXX")"
    if ! jq "$filter" "$path" > "$tmp"; then
        rm -f "$tmp"
        stack::die "manifest edit failed: $filter"
    fi
    mv "$tmp" "$path"
    stack::debug "manifest edit applied: $filter"
}

# Same as edit, but accepts --arg/--argjson pairs before the filter.
manifest::edit_with_args() {
    local args=()
    while [[ "$1" == "--arg" || "$1" == "--argjson" ]]; do
        args+=("$1" "$2" "$3")
        shift 3
    done
    local filter="$1"
    local path tmp
    path="$(manifest::path)"
    tmp="$(mktemp "${path}.XXXXXX")"
    if ! jq "${args[@]}" "$filter" "$path" > "$tmp"; then
        rm -f "$tmp"
        stack::die "manifest edit failed: $filter"
    fi
    mv "$tmp" "$path"
    stack::debug "manifest edit applied"
}

# Update one branch's commit_sha by name.
manifest::set_branch_sha() {
    local name="$1" sha="$2"
    manifest::edit_with_args --arg name "$name" --arg sha "$sha" \
        '.branches |= map(if .name == $name then .commit_sha = $sha else . end)'
}

# Refresh top-level verification with the new stack tip's tree. Preserves
# the decomposer's original_tree as historical baseline.
manifest::record_verification() {
    local stack_tip_tree="$1"
    local now
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    manifest::edit_with_args \
        --arg tree "$stack_tip_tree" --arg ts "$now" \
        '.verification.passed = true
         | .verification.method = "tree-hash-equality"
         | .verification.stack_tip_tree = $tree
         | .verification.current_stack_tip_tree = $tree
         | .verification.last_verified_at = $ts'
}

# Write a last_update record. integrated_shas_json must be a JSON array string.
manifest::record_update() {
    local target_branch="$1" strategy="$2" integrated_shas_json="$3"
    local now
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    manifest::edit_with_args \
        --arg target "$target_branch" \
        --arg strategy "$strategy" \
        --arg ts "$now" \
        --argjson shas "$integrated_shas_json" \
        '.last_update = {timestamp: $ts, target_branch: $target, integration_strategy: $strategy, integrated_commit_shas: $shas}'
}

manifest::set_pr() {
    local name="$1" pr_id="$2" pr_url="$3"
    manifest::edit_with_args \
        --arg name "$name" --arg id "$pr_id" --arg url "$pr_url" \
        '.branches |= map(if .name == $name then .pr_id = $id | .pr_url = $url else . end)'
}

# Set-once: write root_pr_id / root_pr_url only if not already populated.
# Preserved across stack land so back-links from later PRs remain valid even
# after the root branch has been removed from the manifest.
manifest::set_root_pr() {
    local pr_id="$1" pr_url="$2"
    manifest::edit_with_args \
        --arg id "$pr_id" --arg url "$pr_url" \
        'if (.root_pr_id // "") == "" then .root_pr_id = $id | .root_pr_url = $url else . end'
}

manifest::root_pr_id() {
    jq -r '.root_pr_id // empty' "$(manifest::path)"
}

# Remove a branch from the array. Remaining branches keep their original
# order values; gaps after a land are intentional.
manifest::drop_branch() {
    local name="$1"
    manifest::edit_with_args --arg name "$name" \
        '.branches |= map(select(.name != $name))'
}

# Set parent_branch for a given branch by name.
manifest::set_parent_branch() {
    local name="$1" parent="$2"
    manifest::edit_with_args --arg name "$name" --arg parent "$parent" \
        '.branches |= map(if .name == $name then .parent_branch = $parent else . end)'
}

# Get optional base_ref. Echoes empty if absent.
manifest::base_ref() {
    jq -r '.base_ref // empty' "$(manifest::path)"
}

manifest::set_base_branch() {
    local sha="$1"
    manifest::edit_with_args --arg sha "$sha" '.base_branch = $sha'
}

manifest::set_base_ref() {
    local ref="$1"
    manifest::edit_with_args --arg ref "$ref" '.base_ref = $ref'
}
