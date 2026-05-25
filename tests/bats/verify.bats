#!/usr/bin/env bats

load test_helper
load fixtures

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    fixture::make_repo_with_stack "$REPO"
    cd "$REPO"
    export STACK_MANIFEST="$REPO/.git/stack/manifests/feat.json"

    load_lib common.sh
    load_lib git_helpers.sh
    load_lib manifest.sh
    load_lib verify.sh
    stack::preflight_repo
}

@test "compute_reference_tip on the recorded SHAs reproduces the stack tip tree" {
    local base
    base="$(jq -r '.base_branch' "$STACK_MANIFEST")"
    local shas
    shas=()
    while IFS=$'\t' read -r order name sha parent; do
        shas+=("$sha")
    done < <(manifest::branches_in_order)

    local ref_tree
    ref_tree="$(verify::compute_reference_tip "$base" "${shas[@]}")"

    local actual_tree
    actual_tree="$(git rev-parse 'feat-4^{tree}')"

    [[ "$ref_tree" == "$actual_tree" ]]
}

@test "verify_tip succeeds when trees match" {
    local expected
    expected="$(git rev-parse 'feat-4^{tree}')"
    run verify::verify_tip "$expected" "feat-4"
    assert_success
}

@test "verify_tip fails and prints a diff when trees differ" {
    local wrong="0000000000000000000000000000000000000000"
    run verify::verify_tip "$wrong" "feat-4"
    assert_failure
    assert_output_contains "verification failed"
}
