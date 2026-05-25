#!/usr/bin/env bats

load test_helper
load fixtures

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    fixture::make_repo_with_stack "$REPO"
    cd "$REPO"
    export STACK_MANIFEST="$REPO/stack-manifest.json"
}

run_status() {
    run "$STACK_HOME/bin/stack" status --structured "$@"
}

@test "clean stack reports no drift" {
    run_status
    assert_success
    [[ "$output" != *"drift MISSING_BRANCH"* ]]
    [[ "$output" != *"drift BRANCH_MOVED"* ]]
    [[ "$output" != *"drift PARENT_DRIFT"* ]]
    [[ "$output" != *"drift DIRTY_WORKTREE"* ]]
}

@test "missing local branch is reported as MISSING_BRANCH" {
    git switch main --quiet
    git branch -D feat-3 --quiet 2>/dev/null || git branch -D feat-3
    run_status
    assert_success
    assert_output_contains "drift MISSING_BRANCH branch=feat-3"
}

@test "uncommitted commit on current stack branch is UNCOMMITTED_AHEAD" {
    git switch feat-2 --quiet
    printf 'extra\n' >> b.txt
    git add b.txt
    git commit --quiet -m "extra on feat-2"
    run_status
    assert_success
    assert_output_contains "drift UNCOMMITTED_AHEAD branch=feat-2"
}

@test "branch moved while not checked out is BRANCH_MOVED" {
    git switch feat-2 --quiet
    printf 'extra\n' >> b.txt
    git add b.txt
    git commit --quiet -m "extra on feat-2"
    git switch main --quiet
    run_status
    assert_success
    assert_output_contains "drift BRANCH_MOVED branch=feat-2"
}

@test "dirty working tree is reported as DIRTY_WORKTREE" {
    printf 'dirty\n' >> a.txt
    run_status
    assert_success
    assert_output_contains "drift DIRTY_WORKTREE"
}
