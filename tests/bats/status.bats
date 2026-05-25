#!/usr/bin/env bats

load test_helper
load fixtures

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    fixture::make_repo_with_stack "$REPO"
    cd "$REPO"
    export STACK_MANIFEST="$REPO/.git/stack/manifests/feat.json"
}

run_status() {
    run "$STACK_HOME/bin/stack" status --stack=feat "$@"
}

@test "clean stack reports no drift" {
    run_status
    assert_success
    assert_output_contains "no drift"
}

@test "missing local branch is reported as MISSING_BRANCH" {
    git switch main --quiet
    git branch -D feat-3 --quiet 2>/dev/null || git branch -D feat-3
    run_status
    assert_success
    assert_output_contains "MISSING_BRANCH"
    assert_output_contains "feat-3"
}

@test "uncommitted commit on current stack branch is UNCOMMITTED_AHEAD" {
    git switch feat-2 --quiet
    printf 'extra\n' >> b.txt
    git add b.txt
    git commit --quiet -m "extra on feat-2"
    run_status
    assert_success
    assert_output_contains "UNCOMMITTED_AHEAD"
    assert_output_contains "feat-2"
}

@test "branch moved while not checked out is BRANCH_MOVED" {
    git switch feat-2 --quiet
    printf 'extra\n' >> b.txt
    git add b.txt
    git commit --quiet -m "extra on feat-2"
    git switch main --quiet
    run_status
    assert_success
    assert_output_contains "BRANCH_MOVED"
    assert_output_contains "feat-2"
}

@test "dirty working tree is reported as a precondition" {
    printf 'dirty\n' >> a.txt
    run_status
    assert_success
    assert_output_contains "preconditions not met"
    assert_output_contains "uncommitted changes"
}
