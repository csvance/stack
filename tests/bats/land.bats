#!/usr/bin/env bats

load test_helper
load fixtures

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    fixture::make_repo_with_stack "$REPO"
    cd "$REPO"
    export STACK_MANIFEST="$REPO/stack-manifest.json"

    ORIGIN="$BATS_TEST_TMPDIR/origin.git"
    git init --bare --quiet "$ORIGIN"
    git remote add origin "$ORIGIN"
    git push --quiet origin main feat-1 feat-2 feat-3 feat-4

    jq '. + {base_ref: "origin/main"}' stack-manifest.json > tmp.json
    mv tmp.json stack-manifest.json
}

@test "land: no-op when bottom not merged" {
    git fetch --quiet origin main
    run "$STACK_HOME/bin/stack" land --yes --no-fetch --no-az
    assert_success
    assert_output_contains "nothing to land"
}

@test "land: drops bottom, renumbers manifest, rebases the rest" {
    # Simulate "bottom merged": advance origin/main to include feat-1's commit
    # so merge-base detection treats it as merged.
    local clone="$BATS_TEST_TMPDIR/clone"
    git clone --quiet "$ORIGIN" "$clone"
    (
        cd "$clone"
        git config user.email t@e.com
        git config user.name t
        git fetch --quiet origin feat-1
        git checkout --quiet main
        git merge --ff-only origin/feat-1 --quiet || git merge --no-edit origin/feat-1 --quiet
        git push --quiet origin main
    )

    run "$STACK_HOME/bin/stack" land --yes --no-az
    assert_success

    # feat-1 should be gone locally.
    ! git show-ref --verify --quiet refs/heads/feat-1

    # Manifest should have 3 branches now, with feat-2,3,4 renumbered to 1,2,3.
    local total
    total="$(jq -r '.branches | length' stack-manifest.json)"
    [[ "$total" == 3 ]]
    [[ "$(jq -r '.branches[0].name' stack-manifest.json)" == "feat-2" ]]
    [[ "$(jq -r '.branches[0].order' stack-manifest.json)" == 1 ]]
    [[ "$(jq -r '.branches[2].name' stack-manifest.json)" == "feat-4" ]]
    [[ "$(jq -r '.branches[2].order' stack-manifest.json)" == 3 ]]
}
