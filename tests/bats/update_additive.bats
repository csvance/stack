#!/usr/bin/env bats

load test_helper
load fixtures

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    fixture::make_repo_with_stack "$REPO"
    cd "$REPO"
    export STACK_MANIFEST="$REPO/.git/stack/manifests/feat.json"
}

@test "additive: leaves user commits on the branch, rebuilds downstream, updates manifest" {
    git switch feat-2 --quiet
    echo "user-change" >> b.txt
    git add b.txt
    git commit --quiet -m "user: extra"

    run "$STACK_HOME/bin/stack" update --strategy=additive --yes
    assert_success

    # feat-2 should now have one MORE commit than before (the user's).
    [[ "$(git log --pretty='%h' main..feat-2 | wc -l | tr -d ' ')" == 3 ]]

    # The user's b.txt addition should be visible at every downstream tip.
    git show feat-3:b.txt | grep -q user-change
    git show feat-4:b.txt | grep -q user-change

    # Manifest's feat-2 commit_sha must equal the current feat-2 tip.
    local feat2_manifest
    feat2_manifest="$(jq -r '.branches[] | select(.name=="feat-2") | .commit_sha' "$STACK_MANIFEST")"
    [[ "$feat2_manifest" == "$(git rev-parse feat-2)" ]]

    [[ "$(jq -r '.last_update.integration_strategy' "$STACK_MANIFEST")" == "additive" ]]
}
