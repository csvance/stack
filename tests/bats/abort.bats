#!/usr/bin/env bats

load test_helper
load fixtures

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    fixture::make_repo_with_stack "$REPO"
    cd "$REPO"
    export STACK_MANIFEST="$REPO/.git/stack/manifests/feat.json"
}

run_abort() {
    run "$STACK_HOME/bin/stack" abort "$@"
}

@test "abort with no backups is a clean no-op" {
    run_abort
    assert_success
    assert_output_contains "nothing to abort"
}

@test "abort --list with no backups reports nothing" {
    run_abort --list
    assert_success
    assert_output_contains "no backups exist"
}

@test "abort restores branches from the latest snapshot" {
    load_lib common.sh
    load_lib manifest.sh
    load_lib backup.sh
    stack::preflight_repo

    local original_sha
    original_sha="$(git rev-parse feat-2)"
    backup::snapshot test feat-1 feat-2 feat-3 feat-4 > /dev/null

    fixture::add_commit_to_branch "$REPO" feat-2 b.txt "extra"
    cd "$REPO"
    git switch main --quiet

    run_abort --yes
    assert_success

    local restored_sha
    restored_sha="$(git rev-parse feat-2)"
    [[ "$restored_sha" == "$original_sha" ]]
}
