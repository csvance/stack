#!/usr/bin/env bats

load test_helper
load fixtures

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    fixture::make_repo_with_stack "$REPO"
    cd "$REPO"
    export STACK_MANIFEST="$REPO/.git/stack/manifests/feat.json"

    load_lib common.sh
    load_lib manifest.sh
    stack::preflight_repo
}

@test "manifest::load accepts a freshly built fixture" {
    run manifest::load
    assert_success
}

@test "manifest::load rejects a missing required field" {
    jq 'del(.stack_prefix)' "$STACK_MANIFEST" > tmp.json
    mv tmp.json "$STACK_MANIFEST"
    run manifest::load
    assert_failure
    assert_output_contains "stack_prefix"
}

@test "manifest::load accepts non-contiguous order values (after a land)" {
    # Simulate a stack where feat-1 has been landed: orders 2, 3, 4 remain.
    jq '.branches |= map(select(.order != 1))' "$STACK_MANIFEST" > tmp.json
    mv tmp.json "$STACK_MANIFEST"
    run manifest::load
    assert_success
}

@test "manifest::load rejects duplicate order values" {
    jq '.branches[1].order = 1' "$STACK_MANIFEST" > tmp.json
    mv tmp.json "$STACK_MANIFEST"
    run manifest::load
    assert_failure
    assert_output_contains "unique"
}

@test "manifest::load rejects non-positive order values" {
    jq '.branches[0].order = 0' "$STACK_MANIFEST" > tmp.json
    mv tmp.json "$STACK_MANIFEST"
    run manifest::load
    assert_failure
    assert_output_contains "positive integer"
}

@test "manifest::branches_in_order returns each branch with TSV fields" {
    run manifest::branches_in_order
    assert_success
    [[ "$(printf '%s\n' "$output" | wc -l | tr -d ' ')" == 4 ]]
    [[ "$output" == *"feat-1"* ]]
    [[ "$output" == *"feat-4"* ]]
}

@test "manifest::set_branch_sha updates only the named branch" {
    manifest::set_branch_sha "feat-2" "deadbeef"
    run manifest::branch_field "feat-2" "commit_sha"
    [[ "$output" == "deadbeef" ]]
    run manifest::branch_field "feat-1" "commit_sha"
    [[ "$output" != "deadbeef" ]]
}

@test "manifest::record_update writes the last_update block" {
    manifest::record_update "feat-2" "squash" '["aaa","bbb"]'
    run jq -r '.last_update.target_branch' "$STACK_MANIFEST"
    [[ "$output" == "feat-2" ]]
    run jq -r '.last_update.integration_strategy' "$STACK_MANIFEST"
    [[ "$output" == "squash" ]]
    run jq -r '.last_update.integrated_commit_shas | length' "$STACK_MANIFEST"
    [[ "$output" == 2 ]]
}

@test "manifest::set_pr populates pr_id and pr_url for a single branch" {
    manifest::set_pr "feat-3" "42" "https://example/PR/42"
    run jq -r '.branches[] | select(.name=="feat-3") | .pr_id' "$STACK_MANIFEST"
    [[ "$output" == "42" ]]
    run jq -r '.branches[] | select(.name=="feat-1") | .pr_id // ""' "$STACK_MANIFEST"
    [[ "$output" == "" ]]
}

@test "manifest::record_verification preserves original_tree and writes new fields" {
    local original_tree
    original_tree="$(jq -r '.verification.original_tree' "$STACK_MANIFEST")"
    manifest::record_verification "abcd1234"
    run jq -r '.verification.original_tree' "$STACK_MANIFEST"
    [[ "$output" == "$original_tree" ]]
    run jq -r '.verification.current_stack_tip_tree' "$STACK_MANIFEST"
    [[ "$output" == "abcd1234" ]]
}
