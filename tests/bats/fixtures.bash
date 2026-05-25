# Fixture builders. Sourced by tests that need a populated git repo.

# fixture::make_repo_with_stack <repo-dir>
#
# Creates a git repo with:
#   - a `main` base branch holding one commit touching a.txt, b.txt, c.txt, d.txt
#   - four stack branches feat-1..feat-4, each adding one commit on top of the
#     previous, each touching one of those files
#   - a manifest at .git/stack/manifests/feat.json describing the stack
#
# Initializes git-branchless. Skips the test if branchless is unavailable.
fixture::make_repo_with_stack() {
    local repo="$1"
    require_branchless

    mkdir -p "$repo"
    (
        cd "$repo"
        git init --quiet --initial-branch=main
        git config user.email "test@example.com"
        git config user.name  "stack-test"
        git config commit.gpgsign false

        for f in a b c d; do
            printf 'base %s\n' "$f" > "$f.txt"
        done
        git add a.txt b.txt c.txt d.txt
        git commit --quiet -m "base"

        local base_sha; base_sha="$(git rev-parse HEAD)"
        local base_tree; base_tree="$(git rev-parse 'HEAD^{tree}')"

        git branchless init --quiet >/dev/null 2>&1 || git branchless init >/dev/null 2>&1

        # feat-1 .. feat-4, each adding a line to a different file.
        local file letters=(a b c d) i
        for i in 1 2 3 4; do
            local prev parent
            if (( i == 1 )); then
                parent="main"
            else
                parent="feat-$((i-1))"
            fi
            git switch -c "feat-$i" "$parent" --quiet
            file="${letters[$((i-1))]}.txt"
            printf 'feat %s\n' "$i" >> "$file"
            git add "$file"
            git commit --quiet -m "feat $i: edit $file" -m "Body for feat $i."
        done

        # Build manifest.
        local stack_prefix="feat"
        local stack_tip_tree; stack_tip_tree="$(git rev-parse "feat-4^{tree}")"
        mkdir -p .git/stack/manifests
        jq -n \
            --arg created "2026-05-24T00:00:00Z" \
            --arg base "$base_sha" \
            --arg orig_feat "feat" \
            --arg orig_tip "$(git rev-parse feat-4)" \
            --arg prefix "$stack_prefix" \
            --arg base_tree "$base_tree" \
            --arg tip_tree "$stack_tip_tree" \
            --argjson branches "$(
                for i in 1 2 3 4; do
                    local sha parent_name parent_val files subject body
                    sha="$(git rev-parse "feat-$i")"
                    if (( i == 1 )); then
                        parent_val="$base_sha"
                    else
                        parent_val="feat-$((i-1))"
                    fi
                    files="$(git diff-tree -r --no-commit-id --name-only "$sha" | jq -R . | jq -s .)"
                    subject="$(git log -1 --pretty='%s' "$sha")"
                    body="$(git log -1 --pretty='%b' "$sha")"
                    jq -n \
                        --argjson order "$i" \
                        --arg name "feat-$i" \
                        --arg sha "$sha" \
                        --arg subject "$subject" \
                        --arg body "$body" \
                        --arg parent "$parent_val" \
                        --argjson files "$files" \
                        '{order:$order, name:$name, commit_sha:$sha, commit_subject:$subject, commit_body:$body, parent_branch:$parent, files_changed:$files}'
                done | jq -s .
            )" \
            '{
                version: 1,
                created_at: $created,
                base_branch: $base,
                original_feature_branch: $orig_feat,
                original_tip_commit: $orig_tip,
                stack_prefix: $prefix,
                branches: $branches,
                verification: {
                    passed: true,
                    method: "tree-hash-equality",
                    original_tree: $tip_tree,
                    stack_tip_tree: $tip_tree
                }
            }' > .git/stack/manifests/feat.json

        git switch main --quiet
    )
}

# fixture::add_commit_to_branch <repo> <branch> <file> <content>
fixture::add_commit_to_branch() {
    local repo="$1" branch="$2" file="$3" content="$4"
    (
        cd "$repo"
        git switch "$branch" --quiet
        printf '%s\n' "$content" >> "$file"
        git add "$file"
        git commit --quiet -m "extra commit on $branch"
    )
}
