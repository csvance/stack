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
    load_lib backup.sh
    stack::preflight_repo
}

@test "snapshot creates one ref per branch under the set id" {
    local set_id
    set_id="$(backup::snapshot test feat-1 feat-2 feat-3 feat-4)"
    [[ "$set_id" == refs/backup/stack/feat/test-* ]]

    local count
    count="$(git for-each-ref --format='%(refname)' "$set_id/" | wc -l | tr -d ' ')"
    [[ "$count" == 4 ]]
}

@test "list_sets returns previously created sets newest first" {
    backup::snapshot first feat-1 > /dev/null
    sleep 1
    backup::snapshot second feat-1 > /dev/null

    local first_line
    first_line="$(backup::list_sets | head -n 1)"
    [[ "$first_line" == *"second-"* ]]
}

@test "restore returns branches to their recorded SHAs" {
    local original_sha
    original_sha="$(git rev-parse feat-2)"
    local set_id
    set_id="$(backup::snapshot test feat-2)"

    # Move feat-2 forward by adding a commit.
    fixture::add_commit_to_branch "$REPO" feat-2 b.txt "extra"
    cd "$REPO"
    local moved_sha
    moved_sha="$(git rev-parse feat-2)"
    [[ "$moved_sha" != "$original_sha" ]]

    # Switch off the branch so restore doesn't need to reset HEAD.
    git switch main --quiet

    backup::restore "$set_id"

    local final_sha
    final_sha="$(git rev-parse feat-2)"
    [[ "$final_sha" == "$original_sha" ]]
}

@test "latest_set returns the most recent set id" {
    backup::snapshot one feat-1 > /dev/null
    sleep 1
    local newest
    newest="$(backup::snapshot two feat-1)"
    local latest
    latest="$(backup::latest_set)"
    [[ "$latest" == "$newest" ]]
}
