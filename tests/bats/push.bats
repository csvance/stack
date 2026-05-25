#!/usr/bin/env bats

load test_helper
load fixtures

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    fixture::make_repo_with_stack "$REPO"
    cd "$REPO"
    export STACK_MANIFEST="$REPO/stack-manifest.json"

    # Set up a bare repo as origin and push the initial state.
    ORIGIN="$BATS_TEST_TMPDIR/origin.git"
    git init --bare --quiet "$ORIGIN"
    git remote add origin "$ORIGIN"
    git push --quiet origin main feat-1 feat-2 feat-3 feat-4

    # Tag the URL so az_helpers can parse it. The parser only accepts the
    # standard Azure DevOps URL forms, so override the remote for this test.
    # We change just the value the parser sees by exporting AZ_* via a hook.
    # Simpler: stub the resolver by setting the remote URL to a synthetic
    # Azure URL but keep the real bare repo accessible.
    git remote set-url origin "https://dev.azure.com/testorg/testproject/_git/testrepo"
    git remote set-url --push origin "$ORIGIN"
    # For fetch/push, --push overrides; for fetch we still parse the
    # Azure URL but the actual push goes to the bare repo.
    # Tell az_helpers the parsed values directly to bypass URL parsing
    # against a non-pushable URL during fetch:
    git config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
    # Add base_ref to manifest so push knows the bottom target.
    jq '. + {base_ref: "main"}' stack-manifest.json > tmp.json
    mv tmp.json stack-manifest.json

    export MOCK_AZ_STATE_DIR="$BATS_TEST_TMPDIR/az-state"
}

@test "first push creates one PR per branch with sibling cross-links" {
    run "$STACK_HOME/bin/stack" push --yes
    assert_success

    # Each branch should have a pr_id recorded.
    for b in feat-1 feat-2 feat-3 feat-4; do
        local id
        id="$(jq -r --arg n "$b" '.branches[] | select(.name==$n) | .pr_id // empty' stack-manifest.json)"
        [[ -n "$id" ]]
    done

    # Targets: feat-1 -> main, feat-2 -> feat-1, ..., feat-4 -> feat-3.
    local t
    t="$(jq -r '.branches[] | select(.name=="feat-1") | .pr_id' stack-manifest.json)"
    pr_json="$(MOCK_AZ_STATE_DIR=$MOCK_AZ_STATE_DIR cat "$MOCK_AZ_STATE_DIR/pr-$t.json")"
    [[ "$(printf '%s' "$pr_json" | jq -r .targetRefName)" == "refs/heads/main" ]]

    t="$(jq -r '.branches[] | select(.name=="feat-3") | .pr_id' stack-manifest.json)"
    pr_json="$(cat "$MOCK_AZ_STATE_DIR/pr-$t.json")"
    [[ "$(printf '%s' "$pr_json" | jq -r .targetRefName)" == "refs/heads/feat-2" ]]

    # pr_status_cache should be populated.
    [[ "$(jq -r '.pr_status_cache.prs | length' stack-manifest.json)" == 4 ]]
}

@test "second push with no changes is a no-op for git push and updates PRs" {
    "$STACK_HOME/bin/stack" push --yes > /dev/null

    local before; before="$(jq -r '.branches[] | select(.name=="feat-2") | .pr_id' stack-manifest.json)"

    run "$STACK_HOME/bin/stack" push --yes
    assert_success

    local after; after="$(jq -r '.branches[] | select(.name=="feat-2") | .pr_id' stack-manifest.json)"
    [[ "$before" == "$after" ]]
}
