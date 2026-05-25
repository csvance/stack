#!/usr/bin/env bats

load test_helper
load fixtures

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    fixture::make_repo_with_stack "$REPO"
    cd "$REPO"
    export STACK_MANIFEST="$REPO/.git/stack/manifests/feat.json"

    # Set up a bare repo as origin and push initial state.
    ORIGIN="$BATS_TEST_TMPDIR/origin.git"
    git init --bare --quiet "$ORIGIN"
    git remote add origin "$ORIGIN"
    git push --quiet origin main feat-1 feat-2 feat-3 feat-4

    # Add base_ref to manifest.
    jq '. + {base_ref: "origin/main"}' "$STACK_MANIFEST" > tmp.json
    mv tmp.json "$STACK_MANIFEST"
}

@test "sync is a no-op when origin/main has not moved" {
    git fetch --quiet origin main
    run "$STACK_HOME/bin/stack" sync --yes --no-fetch
    assert_success
    assert_output_contains "base unchanged"
}

@test "sync rebases the stack when origin/main advances" {
    # Advance main on the bare origin: add an unrelated commit.
    local clone="$BATS_TEST_TMPDIR/clone"
    git clone --quiet "$ORIGIN" "$clone"
    (
        cd "$clone"
        git config user.email t@e.com
        git config user.name t
        git checkout --quiet main
        echo "upstream" > upstream.txt
        git add upstream.txt
        git commit --quiet -m "upstream change"
        git push --quiet origin main
    )

    local old_base; old_base="$(jq -r '.base_branch' "$STACK_MANIFEST")"
    run "$STACK_HOME/bin/stack" sync --yes
    assert_success

    local new_base; new_base="$(jq -r '.base_branch' "$STACK_MANIFEST")"
    [[ "$new_base" != "$old_base" ]]

    # All four stack branches should now descend from the new base.
    for b in feat-1 feat-2 feat-3 feat-4; do
        git merge-base --is-ancestor "$new_base" "$b"
    done

    # The upstream file should be present at every stack branch tip.
    for b in feat-1 feat-2 feat-3 feat-4; do
        git show "$b:upstream.txt" >/dev/null
    done
}
