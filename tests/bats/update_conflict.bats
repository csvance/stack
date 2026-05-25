#!/usr/bin/env bats

load test_helper
load fixtures

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    fixture::make_repo_with_stack "$REPO"
    cd "$REPO"
    export STACK_MANIFEST="$REPO/.git/stack/manifests/feat.json"

    # Point STACK_PYCHARM at our mock; tests below set MOCK_PYCHARM_MODE.
    export STACK_PYCHARM="$STACK_HOME/tests/bats/mocks/pycharm"
}

@test "conflict (resolve_clean): rebuild succeeds and manifest is updated" {
    export MOCK_PYCHARM_MODE=resolve_clean

    # Force a conflict on c.txt (which feat-3 also touches).
    git switch feat-2 --quiet
    printf 'collide\n' >> c.txt
    git add c.txt
    git commit --quiet -m "user: edit c.txt"

    run "$STACK_HOME/bin/stack" update --strategy=additive --yes
    assert_success
    assert_output_contains "conflict resolution(s) occurred during rebuild"

    # Manifest must reflect the new SHAs.
    [[ "$(jq -r '.branches[] | select(.name==\"feat-2\") | .commit_sha' "$STACK_MANIFEST")" == "$(git rev-parse feat-2)" ]]
    [[ "$(jq -r '.branches[] | select(.name==\"feat-4\") | .commit_sha' "$STACK_MANIFEST")" == "$(git rev-parse feat-4)" ]]
}

@test "conflict (resolve_with_markers): abort + restore snapshot" {
    export MOCK_PYCHARM_MODE=resolve_with_markers

    local feat3_orig
    feat3_orig="$(git rev-parse feat-3)"

    git switch feat-2 --quiet
    printf 'collide\n' >> c.txt
    git add c.txt
    git commit --quiet -m "user: edit c.txt"

    # The mock leaves conflict markers. resolve_pending will see them and
    # prompt; under STACK_YES=1, prompt::choice errors. Test by responding 'a'
    # via STACK_PYCHARM_TEST_RESPONSE? simpler: make stdin a pipe with 'a'.
    run bash -c "echo a | STACK_YES=0 STACK_DRY_RUN=0 '$STACK_HOME/bin/stack' update --strategy=additive"
    assert_failure

    # The snapshot should have restored feat-3 to its original SHA.
    [[ "$(git rev-parse feat-3)" == "$feat3_orig" ]]
}
