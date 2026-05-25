#!/usr/bin/env bats

load test_helper
load fixtures

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    fixture::make_repo_with_stack "$REPO"
    cd "$REPO"
    export STACK_MANIFEST="$REPO/stack-manifest.json"
}

@test "squash: folds new commits into the modified branch, rebuilds downstream, refreshes manifest" {
    git switch feat-2 --quiet
    echo "user-change" >> b.txt
    git add b.txt
    git commit --quiet -m "user: extra"

    local feat2_recorded
    feat2_recorded="$(jq -r '.branches[] | select(.name=="feat-2") | .commit_sha' stack-manifest.json)"

    run "$STACK_HOME/bin/stack" update --strategy=squash --yes
    assert_success

    # feat-2 should have exactly the same commit count as before the user
    # added their commit (squash folded it in).
    [[ "$(git log --pretty='%h' main..feat-2 | wc -l | tr -d ' ')" == 2 ]]

    # feat-2 message should be the original "feat 2: edit b.txt", not the user's.
    [[ "$(git log -1 --pretty='%s' feat-2)" == "feat 2: edit b.txt" ]]

    # The user's b.txt addition should be visible at every downstream tip.
    git show feat-2:b.txt | grep -q user-change
    git show feat-3:b.txt | grep -q user-change
    git show feat-4:b.txt | grep -q user-change

    # Manifest's feat-2 commit_sha must have been updated.
    local feat2_new
    feat2_new="$(jq -r '.branches[] | select(.name=="feat-2") | .commit_sha' stack-manifest.json)"
    [[ "$feat2_new" != "$feat2_recorded" ]]
    [[ "$feat2_new" == "$(git rev-parse feat-2)" ]]

    # last_update should be a squash record.
    [[ "$(jq -r '.last_update.integration_strategy' stack-manifest.json)" == "squash" ]]
}
