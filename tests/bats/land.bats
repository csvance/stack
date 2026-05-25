#!/usr/bin/env bats

load test_helper
load fixtures

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    fixture::make_repo_with_stack "$REPO"
    cd "$REPO"
    export STACK_MANIFEST="$REPO/.git/stack/manifests/feat.json"

    ORIGIN="$BATS_TEST_TMPDIR/origin.git"
    git init --bare --quiet "$ORIGIN"
    git remote add origin "$ORIGIN"
    git push --quiet origin main feat-1 feat-2 feat-3 feat-4

    jq '. + {base_ref: "origin/main"}' "$STACK_MANIFEST" > tmp.json
    mv tmp.json "$STACK_MANIFEST"
}

@test "land: no-op when bottom not merged" {
    git fetch --quiet origin main
    run "$STACK_HOME/bin/stack" land --yes --no-fetch --no-az
    assert_success
    assert_output_contains "nothing to land"
}

@test "land: drops bottom and preserves remaining order values, rebases the rest" {
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

    # Manifest should have 3 branches now. Orders 2, 3, 4 are preserved
    # (no renumbering); feat-2 remains order 2, feat-4 remains order 4.
    local total
    total="$(jq -r '.branches | length' "$STACK_MANIFEST")"
    [[ "$total" == 3 ]]
    [[ "$(jq -r '.branches[0].name' "$STACK_MANIFEST")" == "feat-2" ]]
    [[ "$(jq -r '.branches[0].order' "$STACK_MANIFEST")" == 2 ]]
    [[ "$(jq -r '.branches[2].name' "$STACK_MANIFEST")" == "feat-4" ]]
    [[ "$(jq -r '.branches[2].order' "$STACK_MANIFEST")" == 4 ]]
}

@test "land: drains to empty by removing the manifest when only branch lands" {
    # Build a one-branch stack at .git/stack/manifests/solo.json.
    git switch -c solo-1 main --quiet
    echo solo > solo.txt && git add solo.txt && git commit --quiet -m "solo"
    git push --quiet origin solo-1
    git switch main --quiet

    local solo_sha; solo_sha="$(git rev-parse solo-1)"
    local base_sha; base_sha="$(git rev-parse main)"

    mkdir -p .git/stack/manifests
    cat > .git/stack/manifests/solo.json <<EOF
{
  "version": 1,
  "created_at": "2026-05-25T00:00:00Z",
  "base_branch": "$base_sha",
  "base_ref": "origin/main",
  "original_feature_branch": "solo",
  "original_tip_commit": "$solo_sha",
  "stack_prefix": "solo",
  "branches": [
    {"order": 1, "name": "solo-1", "commit_sha": "$solo_sha", "commit_subject": "solo", "commit_body": "", "parent_branch": "$base_sha", "files_changed": ["solo.txt"]}
  ],
  "verification": {"passed": true, "method": "tree-hash-equality", "original_tree": "$(git rev-parse 'solo-1^{tree}')", "stack_tip_tree": "$(git rev-parse 'solo-1^{tree}')"}
}
EOF

    # Merge solo-1 into main on origin.
    local clone="$BATS_TEST_TMPDIR/clone-solo"
    git clone --quiet "$ORIGIN" "$clone"
    (
        cd "$clone"
        git config user.email t@e.com
        git config user.name t
        git fetch --quiet origin solo-1
        git checkout --quiet main
        git merge --ff-only origin/solo-1 --quiet || git merge --no-edit origin/solo-1 --quiet
        git push --quiet origin main
    )

    run "$STACK_HOME/bin/stack" land --yes --no-az --stack=solo
    assert_success

    # Manifest file should be gone.
    [[ ! -f "$REPO/.git/stack/manifests/solo.json" ]]
}
